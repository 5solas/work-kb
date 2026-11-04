;;; work-kb.el --- work-kb 캡처·연결 프런트엔드 -*- lexical-binding: t; coding: utf-8 -*-

;; 요구: Emacs 30+ (Windows 기준). 외부 패키지 필수 아님.
;; 권장: markdown-mode, consult (있으면 자동으로 쓴다).

;;; Commentary:

;; 설치:
;;   (load "D:/work/work-kb/emacs/work-kb.el")
;;   (setq work-kb-root "D:/work/work-kb")        ; 코드 루트 (scripts/kb.py가 있는 곳)
;;   ;; (setq work-kb-vault "D:/notes/work-kb")   ; 볼트가 따로 있으면. nil이면 코드 루트
;;
;; 접두 키 바꾸기 (둘 중 하나):
;;   (setq work-kb-prefix-key "C-c c")  ; 그리고 이 파일을 다시 load
;;   (keymap-set global-map "<f8>" work-kb-command-map)  ; 직접 붙이기
;;
;; 키 (접두 키 다음):
;;   c 캡처        C 클립보드 이미지   F 이미지 파일   i inbox 메모
;;   t 오늘 daily  s 검색             o 커서 위치 열기  l link 실행
;;   ? 상태        j Jira 작업        g 편찬(ingest → compile)  a 주제 제안 승인
;;
;; g: `kb.py ingest' 뒤 `kb.py compile'을 차례로 한 *work-kb* 버퍼에 돌린다
;;   (C-u g는 둘 다 --dry-run). LLM이 없으면 두 단계 모두 조용히 건너뛴다.
;; a: wiki/_proposals.md를 열고 번호(또는 all)를 받아 y-or-n-p로 확인한 뒤
;;   `kb.py proposals accept <n|all>'을 보낸다.
;;
;; j (Jira 작업 transient): v 보기 · c 댓글(기본 현재 줄) · t 전환(후보 선택) · a 담당 ·
;;   w 워크로그 · n 새 일감 · p 현재 줄 승격 · q 라이브 JQL · S 동기화 · f outbox 다시 보내기 ·
;;   d 완료 전환 제안. 쓰기는 `kb.py act'(outbox)로만 보내고, 전환·생성·승격은 y-or-n-p로 확인한다.
;;
;; Python 호출은 모두 `work-kb--run' 하나를 거친다(D10): 명령줄은 ASCII만
;; (`python -X utf8 scripts/kb.py --argv-stdin'), 실제 인수는 stdin의 UTF-8 JSON 배열.
;; 연결 정보(주소·토큰)는 Emacs가 전혀 모른다.

;;; Code:

(require 'cl-lib)
(require 'subr-x)

(defgroup work-kb nil
  "work-kb: 업무 메모 캡처와 위키 연결."
  :group 'applications
  :prefix "work-kb-")

;;;; 키맵

(defvar work-kb-command-map (make-sparse-keymap)
  "work-kb 명령의 접두 키맵. `work-kb-prefix-key'에 붙는다.
직접 붙이기: (keymap-set global-map \"<f8>\" work-kb-command-map)")

(defconst work-kb--keys
  '(("c" . work-kb-capture)
    ("C" . work-kb-capture-clipboard-image)
    ("F" . work-kb-capture-image-file)
    ("i" . work-kb-capture-inbox)
    ("t" . work-kb-today)
    ("s" . work-kb-search)
    ("o" . work-kb-open-at-point)
    ("l" . work-kb-link)
    ("?" . work-kb-status)
    ("j" . work-kb-jira)
    ("g" . work-kb-ingest)
    ("a" . work-kb-proposals))
  "`work-kb-command-map'의 키와 명령.")

(dolist (entry work-kb--keys)
  (keymap-set work-kb-command-map (car entry) (cdr entry)))

(defvar work-kb--bound-prefix nil
  "지금 `global-map'에 `work-kb-command-map'을 붙여 둔 키 (다시 붙일 때 떼어 낸다).")

(defun work-kb-rebind-prefix (&optional key)
  "`work-kb-command-map'을 KEY(기본 `work-kb-prefix-key')에 다시 붙인다.
전에 붙였던 키는 아직 이 키맵을 가리킬 때만 떼어 낸다. 빈 문자열이나 nil이면 붙이지 않는다."
  (interactive (list (read-string "새 접두 키: " (bound-and-true-p work-kb-prefix-key))))
  (let ((key (or key (bound-and-true-p work-kb-prefix-key))))
    (when (and work-kb--bound-prefix
               (not (equal work-kb--bound-prefix key))
               (eq (keymap-lookup global-map work-kb--bound-prefix) work-kb-command-map))
      (keymap-unset global-map work-kb--bound-prefix t))
    (if (and (stringp key) (not (string-empty-p key)))
        (progn (keymap-set global-map key work-kb-command-map)
               (setq work-kb--bound-prefix key))
      (setq work-kb--bound-prefix nil))
    (when (called-interactively-p 'any)
      (set-default 'work-kb-prefix-key key)
      (message "work-kb: 접두 키 %s" (or key "없음")))
    key))

;;;; 인코딩 (D11)

(defvar work-kb--coding-patterns nil
  "`file-coding-system-alist'에 넣은 work-kb 패턴 (다시 등록할 때 지운다).")

(defun work-kb--ci-regexp (text)
  "TEXT를 regexp로 인용하되 ASCII 글자는 대소문자를 가리지 않게 한다 (Windows 경로)."
  (mapconcat (lambda (ch)
               (if (or (<= ?a ch ?z) (<= ?A ch ?Z))
                   (format "[%c%c]" (downcase ch) (upcase ch))
                 (regexp-quote (char-to-string ch))))
             text ""))

(defun work-kb--register-coding ()
  "코드 루트와 볼트 아래 `.md'를 UTF-8(EOL 자동)로 읽게 등록한다."
  (setq file-coding-system-alist
        (cl-remove-if (lambda (entry) (member (car-safe entry) work-kb--coding-patterns))
                      file-coding-system-alist))
  (setq work-kb--coding-patterns nil)
  (dolist (dir (delete-dups (delq nil (list (bound-and-true-p work-kb-root)
                                            (bound-and-true-p work-kb-vault)))))
    (when (and (stringp dir) (not (string-empty-p dir)))
      (let ((re (concat "\\`" (work-kb--ci-regexp (file-name-as-directory (expand-file-name dir)))
                        ".*\\.[mM][dD]\\'")))
        (modify-coding-system-alist 'file re 'utf-8)
        (push re work-kb--coding-patterns)))))

(defun work-kb--set-and-register (symbol value)
  (set-default symbol value)
  (work-kb--register-coding))

;;;; 사용자 설정

(defun work-kb--default-root ()
  (let ((file (or load-file-name buffer-file-name)))
    (and file (file-name-as-directory
               (expand-file-name ".." (file-name-directory file))))))

(defcustom work-kb-root (work-kb--default-root)
  "코드 루트 (scripts/kb.py가 있는 폴더). 기본은 이 파일의 상위 폴더."
  :type '(choice (const :tag "없음" nil) directory)
  :set #'work-kb--set-and-register
  :group 'work-kb)

(defcustom work-kb-vault nil
  "볼트 루트 (daily/, inbox/, wiki/ …). nil이면 `work-kb-root'."
  :type '(choice (const :tag "코드 루트와 같음" nil) directory)
  :set #'work-kb--set-and-register
  :group 'work-kb)

(defcustom work-kb-python nil
  "Python 명령. nil이면 자동: <root>/.venv/Scripts/python.exe → PATH의 python
\(WindowsApps 별칭 제외) → `py -3'. 문자열 하나 또는 문자열 목록(예: (\"py\" \"-3\"))."
  :type '(choice (const :tag "자동" nil) string (repeat string))
  :group 'work-kb)

(defcustom work-kb-prefix-key "C-c k"
  "`work-kb-command-map'을 붙일 접두 키. 바꾸면(또는 setq 후 다시 load하면) 다시 붙는다."
  :type '(choice (const :tag "붙이지 않음" nil) key)
  :set (lambda (symbol value)
         (set-default symbol value)
         (work-kb-rebind-prefix value))
  :group 'work-kb)

(defcustom work-kb-process-timeout 120
  "동기 실행(오늘 daily 준비, 스텁 생성)을 기다리는 최대 초."
  :type 'number
  :group 'work-kb)

(defvar work-kb--now-function #'current-time
  "현재 시각을 돌려주는 함수 (테스트 이음매).")

(defconst work-kb-output-buffer "*work-kb*")

;;;; 경로·시간

(defun work-kb--now () (funcall work-kb--now-function))

(defun work-kb--root ()
  "코드 루트 (끝에 /). 없으면 사용자 오류."
  (unless (and (stringp work-kb-root) (file-directory-p work-kb-root))
    (user-error "work-kb-root를 설정하세요 (scripts/kb.py가 있는 폴더): %S" work-kb-root))
  (file-name-as-directory (expand-file-name work-kb-root)))

(defun work-kb--vault ()
  "볼트 루트 (끝에 /)."
  (let ((dir (or work-kb-vault (work-kb--root))))
    (unless (file-directory-p dir)
      (user-error "볼트 폴더가 없습니다: %s" dir))
    (file-name-as-directory (expand-file-name dir))))

(defun work-kb--vault-file-p (file)
  (let ((dir (or work-kb-vault work-kb-root)))
    (and (stringp file) (stringp dir)
         (string-prefix-p (file-name-as-directory (expand-file-name dir))
                          (expand-file-name file) t))))

(defun work-kb--kb-args (command &rest args)
  "kb.py 인수 목록: COMMAND --root <볼트> ARGS..."
  (append (list command "--root" (directory-file-name (work-kb--vault))) args))

(defun work-kb--day (&optional time) (format-time-string "%Y-%m-%d" (or time (work-kb--now))))

(defun work-kb--today-file (&optional time)
  (expand-file-name (format "daily/%s.md" (work-kb--day time)) (work-kb--vault)))

;;;; 파일명 (core.safe_name과 같은 규칙, D16)

(defconst work-kb--reserved-names
  (append '("CON" "PRN" "AUX" "NUL" "CONIN$" "CONOUT$")
          (mapcan (lambda (base)
                    (mapcar (lambda (c) (format "%s%c" base c)) "123456789¹²³"))
                  '("COM" "LPT")))
  "Windows 예약 이름.")

(defun work-kb--safe-name (name &optional max-len)
  "NAME을 Windows 파일명 조각으로 다듬는다. 비면 \"_\", 예약 이름이면 앞에 \"_\"."
  (let* ((max-len (or max-len 80))
         (text (if (fboundp 'ucs-normalize-NFC-string)
                   (ucs-normalize-NFC-string (or name ""))
                 (or name ""))))
    (setq text (replace-regexp-in-string "[<>:\"/\\\\|?*\x00-\x1f]" "" text t t))
    (setq text (replace-regexp-in-string "[ \t\n\r\f\v]+" " " text t t))
    (setq text (string-trim text "[ .]+" "[ .]+"))
    (when (> (length text) max-len)
      (setq text (string-trim-right (substring text 0 max-len) "[ .]+")))
    (cond
     ((string-empty-p text) "_")
     ((member (upcase (string-trim-right (car (split-string text "\\.")) " +"))
              work-kb--reserved-names)
      (concat "_" text))
     (t text))))

(defun work-kb--link-safe-name (name &optional max-len)
  "`work-kb--safe-name' 결과에서 위키링크 안에서 뜻이 있는 `[ ] # ^ |'를 `-'로 바꾼다.
`![[raw/assets/…]]'·`[[inbox/…]]'처럼 링크 대상이 되는 이름(이미지·inbox 파일)에 쓴다."
  (replace-regexp-in-string "[][#^|]" "-" (work-kb--safe-name name max-len) t t))

(defun work-kb--unique-path (path)
  "PATH가 있으면 이름-2.ext, 이름-3.ext … 절대 덮어쓰지 않는다."
  (if (not (file-exists-p path))
      path
    (let ((dir (file-name-directory path))
          (stem (file-name-base path))
          (ext (file-name-extension path t))
          (i 2) found)
      (while (not found)
        (let ((candidate (expand-file-name (format "%s-%d%s" stem i ext) dir)))
          (if (file-exists-p candidate) (setq i (1+ i)) (setq found candidate))))
      found)))

;;;; 프로세스 (D10, D14)

(defun work-kb--find-python-on-path ()
  "exec-path의 python (WindowsApps 별칭 제외)."
  (locate-file "python"
               (cl-remove-if (lambda (dir) (and (stringp dir) (string-match-p "WindowsApps" dir)))
                             exec-path)
               exec-suffixes #'file-executable-p))

(defun work-kb--py-launcher-works-p ()
  (and (executable-find "py")
       (eq 0 (ignore-errors (call-process "py" nil nil nil "-3" "-c" "pass")))))

(defun work-kb--python-command ()
  "Python 명령 목록 (D03). 루트의 .venv는 상대 경로로 돌려준다(명령줄을 ASCII로 유지)."
  (cond
   ((stringp work-kb-python) (list work-kb-python))
   ((consp work-kb-python) (copy-sequence work-kb-python))
   (t
    (let ((root (work-kb--root)))
      (cond
       ((file-exists-p (expand-file-name ".venv/Scripts/python.exe" root))
        (list ".venv/Scripts/python.exe"))
       ((and (not (eq system-type 'windows-nt))
             (file-executable-p (expand-file-name ".venv/bin/python" root)))
        (list ".venv/bin/python"))
       ((work-kb--find-python-on-path) (list (work-kb--find-python-on-path)))
       ((work-kb--py-launcher-works-p) (list "py" "-3"))
       (t (user-error "Python을 찾지 못했습니다. %s.venv를 만들거나 work-kb-python을 설정하세요" root)))))))

(defun work-kb--filter (proc string)
  "읽기 전용 버퍼에도 출력을 넣는 필터."
  (let ((buf (process-buffer proc)))
    (when (buffer-live-p buf)
      (with-current-buffer buf
        (let ((inhibit-read-only t)
              (moving (= (point) (process-mark proc))))
          (save-excursion
            (goto-char (process-mark proc))
            (insert string)
            (set-marker (process-mark proc) (point)))
          (when moving (goto-char (process-mark proc))))))))

(defun work-kb--sentinel (proc _event)
  "끝나면 on-exit(PROC 종료코드)를 부른다. 버퍼에 \"finished\" 같은 글을 넣지 않는다."
  (unless (process-live-p proc)
    (let ((code (process-exit-status proc))
          (callback (process-get proc 'work-kb-on-exit)))
      (process-put proc 'work-kb-exit code)
      (condition-case err
          (if callback
              (funcall callback proc code)
            (work-kb--report proc code))
        (error (message "work-kb: 후처리 오류: %s" (error-message-string err)))))))

(defun work-kb--last-line (buffer)
  (when (buffer-live-p buffer)
    (with-current-buffer buffer
      (car (last (split-string (buffer-string) "\n" t "[ \t\r]+"))))))

(defun work-kb--report (proc code)
  (let ((buf (process-buffer proc)))
    (if (eq code 0)
        (message "work-kb: 완료%s" (let ((line (work-kb--last-line buf))) (if line (concat " — " line) "")))
      (when (buffer-live-p buf) (display-buffer buf))
      (message "work-kb: 실패 (종료 %s). %s 버퍼를 보세요" code (if (buffer-live-p buf) (buffer-name buf) "")))))

(cl-defun work-kb--run (args &key on-exit buffer append header)
  "kb.py를 비동기로 실행한다. Python을 부르는 유일한 입구 (D10).
명령줄은 `python -X utf8 scripts/kb.py --argv-stdin'(ASCII), ARGS(문자열 목록)는 stdin의
JSON 배열로 보낸다. 출력은 BUFFER(기본 *work-kb*, 실행 전에 비움; APPEND면 비우지 않고
끝에 잇는다). HEADER(문자열)가 있으면 출력 앞에 넣는다. 끝나면 ON-EXIT를
\(PROC 종료코드)로 부른다. 프로세스를 돌려준다."
  (unless (and (listp args) (cl-every #'stringp args))
    (error "work-kb--run: ARGS는 문자열 목록이어야 합니다: %S" args))
  (let* ((root (work-kb--root))
         (default-directory root)
         (python (work-kb--python-command))
         ;; 상대 경로 인터프리터(.venv/Scripts/python.exe)를 루트 기준으로 찾게 한다.
         (exec-path (cons root exec-path))
         (buf (get-buffer-create (or buffer work-kb-output-buffer)))
         (command (append python (list "-X" "utf8" "scripts/kb.py" "--argv-stdin")))
         proc)
    (with-current-buffer buf
      (let ((inhibit-read-only t))
        (unless append (erase-buffer))
        (when header
          (goto-char (point-max))
          (unless (or (bobp) (bolp)) (insert "\n"))
          (insert header)))
      (setq default-directory root))
    (setq proc (make-process :name "work-kb"
                             :buffer buf
                             :command command
                             :coding '(utf-8-dos . utf-8-unix)
                             :connection-type 'pipe
                             :noquery t
                             :filter #'work-kb--filter
                             :sentinel #'work-kb--sentinel))
    ;; 버퍼를 꼭 넘긴다: 빠뜨리면 표시가 지금 버퍼를 가리켜 출력이 머리말 앞(1)에 들어간다.
    (set-marker (process-mark proc) (with-current-buffer buf (point-max)) buf)
    (process-put proc 'work-kb-on-exit on-exit)
    (process-send-string proc (json-serialize (vconcat args)))
    (process-send-eof proc)
    proc))

(defun work-kb--wait (proc &optional timeout)
  "PROC가 끝나고 센티널이 돌 때까지 기다린다. 종료 코드, 시간 초과면 nil."
  (let ((deadline (+ (float-time) (or timeout work-kb-process-timeout))))
    (while (and (null (process-get proc 'work-kb-exit))
                (< (float-time) deadline))
      (accept-process-output (and (process-live-p proc) proc) 0.05))
    (or (process-get proc 'work-kb-exit)
        (progn (when (process-live-p proc) (delete-process proc)) nil))))

(defun work-kb--run-sync (args &optional timeout)
  "ARGS로 kb.py를 실행하고 끝날 때까지 기다린다. 종료 코드(시간 초과면 nil)."
  (let ((proc (work-kb--run args :on-exit #'ignore)))
    (work-kb--wait proc timeout)))

;;;; daily 쓰기 (AGENTS.md 허용 목록 1: capture가 `## 기록' 끝에 추가)

(defun work-kb--daily-skeleton (day)
  "notes.daily_skeleton과 같은 바이트."
  (concat "---\n" "date: " day "\n" "---\n"
          "## 오늘\n" "<!-- kb:today:start -->\n" "<!-- kb:today:end -->\n" "\n"
          "## 이월\n" "<!-- kb:carryover:start -->\n" "<!-- kb:carryover:end -->\n" "\n"
          "## 기록\n"))

(defun work-kb--write-new-file (path content)
  "PATH에 CONTENT를 UTF-8(LF)로 쓴다. 이미 있으면 오류 (덮어쓰지 않음)."
  (make-directory (file-name-directory path) t)
  (let ((coding-system-for-write 'utf-8-unix))
    (write-region content nil path nil 'silent nil 'excl)))

(defun work-kb--ensure-today (&optional force time)
  "오늘 daily 경로. 없거나 FORCE면 `kb.py today --date'를 기다려 실행하고,
그래도 없으면 뼈대를 직접 만든다 (D15)."
  (let* ((time (or time (work-kb--now)))
         (day (work-kb--day time))
         (file (work-kb--today-file time)))
    (when (or force (not (file-exists-p file)))
      (let ((code (condition-case err
                      (work-kb--run-sync (work-kb--kb-args "today" "--date" day))
                    (error (message "work-kb: today 실행 실패: %s" (error-message-string err)) nil))))
        (unless (eq code 0)
          (message "work-kb: daily_prepare 실패 (종료 %s). %s 버퍼를 보세요" code work-kb-output-buffer))))
    (unless (file-exists-p file)
      (work-kb--write-new-file file (work-kb--daily-skeleton day)))
    file))

(defun work-kb--format-capture (text &optional time)
  "TEXT를 `- HH:MM 내용' 형식으로. `[ ] '로 시작하면 `- [ ] HH:MM 내용' (D15).
여러 줄이면 첫 줄에 시각, 나머지는 두 칸 들여쓰기. 빈 줄은 뺀다."
  (let* ((hhmm (format-time-string "%H:%M" (or time (work-kb--now))))
         (lines (cl-remove-if (lambda (l) (string-empty-p (string-trim l)))
                              (split-string (string-trim (or text "")) "\r?\n")))
         (first (string-trim-right (or (car lines) "")))
         (head (if (string-prefix-p "[ ] " first)
                   (format "- [ ] %s %s" hhmm (string-trim-left (substring first 4)))
                 (format "- %s %s" hhmm first))))
    (when (string-empty-p (string-trim first))
      (user-error "빈 내용은 기록하지 않습니다"))
    (mapconcat #'identity
               (cons head (mapcar (lambda (l) (concat "  " (string-trim-right l))) (cdr lines)))
               "\n")))

(defun work-kb--record-end ()
  "현재 버퍼에서 `## 기록' 절의 마지막 내용 줄 끝 위치. 절이 없으면 nil (D24)."
  (save-excursion
    (save-restriction
      (widen)
      (goto-char (point-min))
      (let ((case-fold-search nil))
        ;; 첫 줄 앞의 BOM(U+FEFF)은 utf-8로 읽으면 글자로 남는다: 있어도 제목으로 본다.
        (when (re-search-forward "^\ufeff?## +기록[ \t\r]*$" nil t)
          (let ((last (line-end-position)))
            (forward-line 1)
            (while (and (not (eobp)) (not (looking-at "## +[^ \t\r\n]")))
              (unless (looking-at "[ \t\r]*$")
                (setq last (line-end-position)))
              (forward-line 1))
            last))))))

(defun work-kb--insert-entry (entry)
  "현재 버퍼의 `## 기록' 끝에 ENTRY 줄을 넣는다. 절이 없으면 파일 끝에 만든다.
버퍼에 CR이 남아 있는 줄(EOL이 섞인 파일) 뒤에는 CRLF로 넣는다."
  (let ((inhibit-read-only t)
        (pos (work-kb--record-end)))
    (save-excursion
      (save-restriction
        (widen)
        (if pos
            (progn
              (goto-char pos)
              (if (eq (char-before pos) ?\r)
                  (insert "\n" (string-replace "\n" "\r\n" entry) "\r")
                (insert "\n" entry)))
          (goto-char (point-max))
          (unless (or (bobp) (bolp)) (insert "\n"))
          (unless (bobp) (insert "\n"))
          (insert "## 기록\n" entry "\n"))))))

(defun work-kb--raw-byte-position ()
  "현재 버퍼에서 UTF-8로 풀리지 않은 바이트(eight-bit 글자)의 첫 위치. 없으면 nil."
  (save-excursion
    (save-restriction
      (widen)
      (goto-char (point-min))
      ;; [[:nonascii:]]는 날 바이트를 건너뛰므로 ASCII를 넘기며 한 글자씩 본다.
      (let (found)
        (while (and (not found) (progn (skip-chars-forward "\0-\177") (not (eobp))))
          (if (eq (char-charset (char-after)) 'eight-bit)
              (setq found (point))
            (forward-char 1)))
        found))))

(defun work-kb--ensure-utf-8-buffer ()
  "버퍼가 UTF-8로 읽혔는지 확인한다. ASCII뿐이면 UTF-8로 바꾸고, 아니면 다시 읽거나 거부한다.
UTF-8로 읽었는데 풀리지 않은 바이트가 있으면(cp949 파일 등) 아무것도 고치지 않고
사용자 오류를 낸다 (D11). 버퍼를 UTF-8로 다시 읽었다면 원래 인코딩으로 되돌린다."
  (let* ((cs (or buffer-file-coding-system 'undecided))
         (base (coding-system-base cs))
         (eol (coding-system-eol-type cs))
         (reverted-from nil))
    (unless (memq base '(utf-8 utf-8-with-signature utf-8-auto prefer-utf-8 utf-8-emacs))
      (cond
       ((memq base '(undecided us-ascii))
        (setq buffer-file-coding-system
              (coding-system-change-eol-conversion 'utf-8 (if (integerp eol) eol 0))))
       ((not (buffer-modified-p))
        (let ((coding-system-for-read 'utf-8))
          (revert-buffer t t t))
        (setq reverted-from cs))
       (t (user-error "%s: UTF-8이 아닌 인코딩(%s)으로 열려 있습니다. 저장 후 다시 여세요"
                      (buffer-name) cs))))
    (when (work-kb--raw-byte-position)
      (when (and reverted-from (not (buffer-modified-p)))
        (let ((coding-system-for-read reverted-from))
          (revert-buffer t t t)))
      (user-error "UTF-8이 아닙니다: %s (UTF-8로 바꿔 저장한 뒤 다시 캡처하세요)"
                  (or buffer-file-name (buffer-name))))
    ;; 줄바꿈이 하나도 없던 파일은 EOL을 알 수 없다: 시스템 기본(CRLF) 대신 LF (D25, 새 파일과 같게).
    (unless (save-excursion (save-restriction (widen) (goto-char (point-min)) (search-forward "\n" nil t)))
      (setq buffer-file-coding-system
            (coding-system-change-eol-conversion buffer-file-coding-system 'unix)))))

(defun work-kb--append-entry (entry &optional time)
  "오늘 daily의 `## 기록' 끝에 ENTRY를 넣고 저장한다. 열린 버퍼가 있으면 그 버퍼를 고친다 (D26)."
  (let* ((file (work-kb--ensure-today nil time))
         (live (find-buffer-visiting file))
         (buf (or live
                  (let ((coding-system-for-read 'utf-8)
                        (find-file-hook nil))
                    (find-file-noselect file t)))))
    (unwind-protect
        (with-current-buffer buf
          (unless (verify-visited-file-modtime buf)
            (if (buffer-modified-p)
                (user-error "%s: 버퍼와 파일이 모두 바뀌었습니다. 먼저 정리하세요" (buffer-name))
              (let ((coding-system-for-read 'utf-8)) (revert-buffer t t t))))
          (work-kb--ensure-utf-8-buffer)
          (work-kb--insert-entry entry)
          (let ((before-save-hook nil)
                (after-save-hook nil)
                (require-final-newline nil)
                (backup-inhibited t)
                (save-silently t))
            (save-buffer)))
      (unless live (kill-buffer buf)))
    file))

;;;; 캡처 버퍼

(defvar-local work-kb--capture-kind 'daily)
(defvar-local work-kb--capture-title nil)
(defvar-local work-kb--capture-window-config nil)

(defvar work-kb-capture-mode-map
  (let ((map (make-sparse-keymap)))
    (keymap-set map "C-c C-c" #'work-kb-capture-finish)
    (keymap-set map "C-c C-k" #'work-kb-capture-cancel)
    map))

(define-derived-mode work-kb-capture-mode text-mode "work-kb 캡처"
  "캡처 버퍼. C-c C-c 저장, C-c C-k 취소."
  (setq header-line-format
        (substitute-command-keys
         "\\<work-kb-capture-mode-map>저장: \\[work-kb-capture-finish]  취소: \\[work-kb-capture-cancel]")))

(defun work-kb--open-capture-buffer (name kind &optional title initial)
  (let ((config (current-window-configuration))
        (buf (get-buffer-create name)))
    (pop-to-buffer buf)
    (unless (derived-mode-p 'work-kb-capture-mode) (work-kb-capture-mode))
    (setq work-kb--capture-kind kind
          work-kb--capture-title title
          work-kb--capture-window-config config)
    (when (and initial (= (buffer-size) 0)) (insert initial))
    buf))

(defun work-kb-capture (&optional text)
  "오늘 daily의 `## 기록' 끝에 `- HH:MM 내용'을 추가한다.
TEXT가 있으면 바로 추가하고, 없으면 캡처 버퍼를 연다 (C-c C-c 저장)."
  (interactive)
  (if text
      (let* ((time (work-kb--now))
             (entry (work-kb--format-capture text time)))
        (work-kb--append-entry entry time)
        (message "work-kb: 기록했습니다 — %s" (car (split-string entry "\n")))
        entry)
    (work-kb--open-capture-buffer
     "*work-kb-capture*" 'daily nil
     (when (use-region-p) (buffer-substring-no-properties (region-beginning) (region-end))))))

(defun work-kb-capture-finish ()
  "캡처 버퍼 내용을 저장하고 닫는다."
  (interactive)
  (let ((text (buffer-substring-no-properties (point-min) (point-max)))
        (kind work-kb--capture-kind)
        (title work-kb--capture-title)
        (config work-kb--capture-window-config)
        (buf (current-buffer)))
    (when (string-empty-p (string-trim text))
      (user-error "빈 내용입니다 (취소: C-c C-k)"))
    (pcase kind
      ('inbox (let ((path (work-kb--write-inbox title text)))
                (message "work-kb: inbox에 저장했습니다 — %s" (file-name-nondirectory path))))
      (_ (work-kb-capture text)))
    (kill-buffer buf)
    (when (window-configuration-p config) (set-window-configuration config))))

(defun work-kb-capture-cancel ()
  "캡처 버퍼를 저장하지 않고 닫는다."
  (interactive)
  (let ((config work-kb--capture-window-config))
    (kill-buffer (current-buffer))
    (when (window-configuration-p config) (set-window-configuration config))
    (message "work-kb: 취소했습니다")))

;;;; 이미지 캡처 (D12, D16)

(defconst work-kb--image-extensions '("png" "jpg" "jpeg" "gif" "webp" "bmp"))

(defun work-kb--asset-path (name ext time)
  "볼트의 raw/assets/YYYY-MM-DD-HHMM-<NAME>.<EXT> (겹치면 -2, -3)."
  (work-kb--unique-path
   (expand-file-name (format "raw/assets/%s-%s.%s"
                             (format-time-string "%Y-%m-%d-%H%M" time)
                             (work-kb--link-safe-name
                              (if (string-empty-p (string-trim (or name ""))) "capture" name))
                             (downcase ext))
                     (work-kb--vault))))

(defun work-kb--embed-entry (asset text time)
  (let ((rel (file-relative-name asset (work-kb--vault)))
        (text (string-trim (or text ""))))
    (work-kb--format-capture (concat "![[" rel "]]" (if (string-empty-p text) "" (concat " " text))) time)))

(defun work-kb--powershell ()
  (expand-file-name "System32/WindowsPowerShell/v1.0/powershell.exe"
                    (or (getenv "SystemRoot") "C:/Windows")))

(defun work-kb--call-clip (out-rel)
  "clip_image.ps1을 OUT-REL(루트 기준 ASCII 상대 경로)로 실행한다. (종료코드 . 출력)."
  (let ((default-directory (work-kb--root))
        ;; 부모 pwsh 7의 모듈 경로·실행 정책이 5.1로 새지 않게 지운다 (D12 보완).
        (process-environment (append '("PSModulePath" "PSExecutionPolicyPreference")
                                     process-environment)))
    (with-temp-buffer
      (let ((code (call-process (work-kb--powershell) nil t nil
                                "-NoProfile" "-NonInteractive" "-ExecutionPolicy" "Bypass" "-STA"
                                "-File" "scripts/clip_image.ps1" "-Out" out-rel)))
        (cons code (string-trim (buffer-string)))))))

(defun work-kb-capture-clipboard-image (&optional input)
  "클립보드 이미지를 raw/assets/에 저장하고 `![[…]] 입력' 줄을 캡처한다."
  (interactive)
  (unless (eq system-type 'windows-nt)
    (user-error "클립보드 이미지 캡처는 Windows에서만 됩니다. F(파일)로 캡처하세요"))
  (let* ((time (work-kb--now))
         (root (work-kb--root))
         (out-rel (format ".state/tmp/clip-%s.png" (format-time-string "%Y%m%d-%H%M%S" time)))
         (out-abs (expand-file-name out-rel root)))
    (make-directory (file-name-directory out-abs) t)
    (pcase-let ((`(,code . ,output) (work-kb--call-clip out-rel)))
      (cond
       ((eq code 2)
        (message "클립보드에 이미지가 없습니다")
        nil)
       ((and (eq code 0) (file-exists-p out-abs))
        (let* ((input (or input (read-string "이미지 이름·설명 (비우면 capture): ")))
               (asset (work-kb--asset-path input "png" time)))
          (make-directory (file-name-directory asset) t)
          (rename-file out-abs asset nil)
          (let ((entry (work-kb--embed-entry asset input time)))
            (work-kb--append-entry entry time)
            (message "work-kb: 이미지를 기록했습니다 — %s" (file-name-nondirectory asset))
            asset)))
       (t
        (when (file-exists-p out-abs) (delete-file out-abs))
        (message "클립보드 이미지를 저장하지 못했습니다 (종료 %s). F(파일)로 캡처하세요. %s"
                 code (or output ""))
        nil)))))

(defun work-kb--dired-file ()
  (and (derived-mode-p 'dired-mode) (fboundp 'dired-get-filename)
       (ignore-errors (dired-get-filename nil t))))

(defun work-kb-capture-image-file (file &optional text)
  "이미지 FILE을 raw/assets/로 복사(덮어쓰지 않음)하고 `![[…]] TEXT' 줄을 캡처한다."
  (interactive
   (let ((default (work-kb--dired-file)))
     (list (read-file-name "이미지 파일: " (and default (file-name-directory default))
                           default t (and default (file-name-nondirectory default)))
           (read-string "설명 (선택): "))))
  (let* ((file (expand-file-name file))
         (ext (downcase (or (file-name-extension file) "")))
         (time (work-kb--now)))
    (unless (file-regular-p file) (user-error "파일이 없습니다: %s" file))
    (unless (member ext work-kb--image-extensions)
      (user-error "이미지 파일이 아닙니다 (%s): %s" (string-join work-kb--image-extensions ", ") file))
    (let ((asset (work-kb--asset-path (file-name-base file) ext time)))
      (make-directory (file-name-directory asset) t)
      (copy-file file asset nil t)
      (work-kb--append-entry (work-kb--embed-entry asset text time) time)
      (message "work-kb: 이미지를 기록했습니다 — %s" (file-name-nondirectory asset))
      asset)))

;;;; inbox (§5.2, D16)

(defun work-kb--inbox-title (title body)
  "TITLE이 비면 BODY의 첫 줄 40자, 그것도 비면 \"메모\"."
  (let ((title (string-trim (or title ""))))
    (if (not (string-empty-p title))
        title
      (let* ((first (cl-find-if (lambda (l) (not (string-empty-p (string-trim l))))
                                (split-string (or body "") "\r?\n")))
             (line (string-trim (replace-regexp-in-string "\\`[ \t]*\\(?:#+\\|[-*]\\)[ \t]+" "" (or first "")))))
        (cond ((string-empty-p line) "메모")
              ((> (length line) 40) (string-trim-right (substring line 0 40)))
              (t line))))))

(defun work-kb--inbox-file-name (title &optional time)
  "inbox 파일 이름: YYYYMMDD-HHMM-<안전한 제목>.md"
  (format "%s-%s.md" (format-time-string "%Y%m%d-%H%M" (or time (work-kb--now)))
          (work-kb--link-safe-name title)))

(defun work-kb--yaml-quote (text)
  (concat "\"" (replace-regexp-in-string
                "[\"\\\\]" (lambda (m) (concat "\\" m))
                (replace-regexp-in-string "[\r\n\t]+" " " text t t) t t)
          "\""))

(defun work-kb--write-inbox (title body &optional time)
  "inbox/에 새 메모를 쓰고 경로를 돌려준다. 이미 있는 파일은 덮지 않는다."
  (let* ((time (or time (work-kb--now)))
         (body (string-trim (or body "")))
         (title (work-kb--inbox-title title body))
         (dir (expand-file-name "inbox" (work-kb--vault)))
         (path (work-kb--unique-path (expand-file-name (work-kb--inbox-file-name title time) dir)))
         (content (concat "---\n"
                          "created: " (format-time-string "%Y-%m-%dT%H:%M:%S%:z" time) "\n"
                          "title: " (work-kb--yaml-quote title) "\n"
                          "---\n"
                          (if (string-empty-p body) "" (concat body "\n")))))
    (work-kb--write-new-file path content)
    path))

(defun work-kb-capture-inbox (&optional title body)
  "제목을 받아 inbox/ 메모를 만든다. BODY가 있으면 바로 쓰고, 없으면 본문 버퍼를 연다."
  (interactive)
  (if body
      (work-kb--write-inbox title body)
    (let ((title (or title (read-string "제목 (비우면 본문 첫 줄): "))))
      (work-kb--open-capture-buffer "*work-kb-inbox*" 'inbox title
                                    (when (use-region-p)
                                      (buffer-substring-no-properties (region-beginning) (region-end)))))))

;;;; 오늘·검색·열기·link·status

(defun work-kb--utf-8-coding-p (cs)
  (and cs (memq (coding-system-base cs) '(utf-8 utf-8-with-signature utf-8-auto prefer-utf-8 utf-8-emacs))))

(defun work-kb-today ()
  "오늘 daily를 준비(`kb.py today')하고 연다. 실패해도 뼈대를 만들어 연다.
오늘 파일을 연 버퍼에 저장하지 않은 수정이 있으면 먼저 저장한다 (캡처와 같은 규칙, D26).
버퍼와 파일이 모두 바뀌었으면 준비를 건너뛰고 버퍼만 보여 준다. 준비 뒤 수정 없는 버퍼는
묻지 않고 다시 읽는다."
  (interactive)
  (let* ((file (work-kb--today-file))
         (live (find-buffer-visiting file))
         (skip nil))
    (when (and live (buffer-modified-p live))
      (with-current-buffer live
        (if (and (file-exists-p file) (not (verify-visited-file-modtime live)))
            (setq skip t)
          (save-buffer))))
    (if skip
        (message "work-kb: %s 버퍼와 파일이 모두 바뀌어 today 준비를 건너뜁니다. 먼저 정리하세요"
                 (buffer-name live))
      (setq file (work-kb--ensure-today t)))
    (when (and (buffer-live-p live)
               (not (buffer-modified-p live))
               (not (verify-visited-file-modtime live)))
      (with-current-buffer live
        (let ((coding-system-for-read (and (work-kb--utf-8-coding-p buffer-file-coding-system) 'utf-8)))
          (revert-buffer t t t))))
    (if (buffer-live-p live)
        (pop-to-buffer-same-window live)
      (find-file file))))

(defun work-kb--search-dirs (vault)
  (cl-remove-if-not (lambda (d) (file-directory-p (expand-file-name d vault)))
                    '("daily" "inbox" "wiki" "jira")))

(defun work-kb--rg-search (rg query vault)
  "rg로 검색해 grep 버퍼에 보인다. 패턴은 stdin(`-f -')으로 보낸다 (D14)."
  (require 'grep)
  (let* ((default-directory vault)
         (buf (get-buffer-create "*work-kb-search*"))
         (dirs (or (work-kb--search-dirs vault) '(".")))
         proc)
    (with-current-buffer buf
      (let ((inhibit-read-only t)) (erase-buffer))
      (setq default-directory vault)
      (grep-mode)
      (setq default-directory vault))
    (setq proc (make-process
                :name "work-kb-rg" :buffer buf
                :command (append (list rg "--line-number" "--no-heading" "--color=never"
                                       "--smart-case" "--fixed-strings" "--path-separator" "/"
                                       "-g" "*.md" "-f" "-")
                                 dirs)
                :coding '(utf-8-dos . utf-8-unix)
                :connection-type 'pipe :noquery t
                :filter #'work-kb--filter
                :sentinel (lambda (p _e)
                            (unless (process-live-p p)
                              (when (and (eq (process-exit-status p) 1) (buffer-live-p (process-buffer p)))
                                (with-current-buffer (process-buffer p)
                                  (let ((inhibit-read-only t))
                                    (goto-char (point-max)) (insert "결과 없음\n"))))))))
    (process-send-string proc (concat query "\n"))
    (process-send-eof proc)
    (display-buffer buf)
    proc))

(defun work-kb-search (query)
  "볼트에서 QUERY 검색: consult-ripgrep → rg(grep 버퍼) → `kb.py search' (D14)."
  (interactive
   (list (read-string "검색: " nil nil (thing-at-point 'symbol t))))
  (let ((query (string-trim (or query "")))
        (vault (work-kb--vault)))
    (when (string-empty-p query) (user-error "검색어가 비었습니다"))
    (cond
     ((fboundp 'consult-ripgrep) (consult-ripgrep vault query))
     ((executable-find "rg") (work-kb--rg-search (executable-find "rg") query vault))
     (t
      (work-kb--run (work-kb--kb-args "search" query)
                    :buffer "*work-kb-search*"
                    :on-exit (lambda (proc code)
                               (let ((buf (process-buffer proc)))
                                 (when (buffer-live-p buf)
                                   (with-current-buffer buf
                                     (when (eq code 0)
                                       (require 'grep)
                                       (grep-mode))
                                     (setq default-directory vault))
                                   (display-buffer buf))
                                 (unless (eq code 0)
                                   (message "work-kb: 검색 실패 (종료 %s)" code)))))))))

(defconst work-kb--key-regexp "[A-Z][A-Z0-9_]+-[0-9]+")

(defun work-kb--key-bounded-p (beg end bol eol)
  "BEG..END의 키 후보가 홀로 선 키인가.
앞뒤에 키 글자나 `-'가 붙어 있으면 아니다.
`xKB-1'·`KB-12-설계'(문서 이름)는 키가 아니다."
  (and (or (= beg bol) (not (string-match-p "[A-Za-z0-9_-]" (string (char-before beg)))))
       (or (= end eol) (not (string-match-p "[A-Za-z0-9_-]" (string (char-after end)))))))

(defun work-kb--link-key (target)
  "위키링크 대상 TARGET이 Jira 키를 가리키면 그 키, 아니면 nil.
앞의 `jira/'와 뒤의 `.md'를 떼고 본다.
[[jira/KB-12]]·[[KB-12.md]] → KB-12, [[KB-12-설계]] → nil."
  (let ((name (let ((case-fold-search t))
                (replace-regexp-in-string
                 "\\.md\\'" "" (replace-regexp-in-string "\\`jira/" "" (string-trim target) t t) t t))))
    (let ((case-fold-search nil))
      (and (string-match-p (concat "\\`" work-kb--key-regexp "\\'") name) name))))

(defun work-kb--thing-at-point ()
  "커서 위치의 대상: (link . 대상) / (key . KEY-1) / (person . 이름) / nil."
  (let ((pos (point))
        (bol (line-beginning-position))
        (eol (line-end-position))
        (case-fold-search nil)
        found)
    (save-excursion
      (goto-char bol)
      (while (and (not found)
                  (re-search-forward "\\[\\[\\([^]|#\n]+\\)\\(?:#[^]|\n]*\\)?\\(?:|[^]\n]*\\)?\\]\\]" eol t))
        (when (<= (match-beginning 0) pos (match-end 0))
          (setq found (cons 'link (string-trim (match-string-no-properties 1))))))
      (goto-char bol)
      (while (and (not found) (re-search-forward work-kb--key-regexp eol t))
        (let ((beg (match-beginning 0)) (end (match-end 0)))
          (when (and (<= beg pos end) (work-kb--key-bounded-p beg end bol eol))
            (setq found (cons 'key (match-string-no-properties 0))))))
      (goto-char bol)
      (while (and (not found) (re-search-forward "@\\([[:alnum:]_]+\\)" eol t))
        (when (and (<= (match-beginning 0) pos (match-end 0))
                   (or (= (match-beginning 0) bol)
                       (not (string-match-p "[A-Za-z0-9._%+-]" (string (char-before (match-beginning 0)))))))
          (setq found (cons 'person (match-string-no-properties 1))))))
    (when (and (eq (car-safe found) 'link)
               (let ((case-fold-search nil))
                 (string-match-p (concat "\\`" work-kb--key-regexp "\\'") (cdr found))))
      (setq found (cons 'key (cdr found))))
    found))

;;;;; Jira 키 열기 (Phase 2: 캐시가 없거나 stub이면 `kb.py sync --key')

(defconst work-kb--exit-reasons
  '((1 . "오류") (2 . "인증") (3 . "네트워크") (4 . "설정") (5 . "공개 호스트 거부")
    (6 . "잠김") (7 . "정책 거부"))
  "kb.py 종료 코드의 뜻 (core.KbError 하위 클래스).")

(defvar work-kb--syncing-keys nil
  "지금 `kb.py sync --key'가 돌고 있는 Jira 키 (같은 키를 두 번 돌리지 않는다).")

(defun work-kb--exit-reason (code)
  (format "종료 %s: %s" code (or (alist-get code work-kb--exit-reasons) "알 수 없음")))

(defun work-kb--jira-file (key)
  (expand-file-name (format "jira/%s.md" key) (work-kb--vault)))

(defun work-kb--sync-buffer-name (key)
  (format "*work-kb-sync %s*" key))

(defun work-kb--jira-stub-p (file)
  "FILE(jira/KEY.md)의 front matter에 `stub: true'가 있으면 t.
앞 8KB만 UTF-8로 읽는다. BOM·CRLF를 허용하고, front matter 밖의 `stub:'은 보지 않는다."
  (with-temp-buffer
    (let ((coding-system-for-read 'utf-8))
      (insert-file-contents file nil 0 8192))
    (goto-char (point-min))
    (when (eq (char-after) ?﻿) (forward-char 1))
    (when (looking-at "---[ \t]*\r?$")
      (forward-line 1)
      (let ((case-fold-search t) found done)
        (while (and (not done) (not (eobp)))
          (cond
           ((looking-at "\\(?:---\\|\\.\\.\\.\\)[ \t]*\r?$") (setq done t))
           ((looking-at "stub[ \t]*:[ \t]*['\"]?\\(?:true\\|yes\\|on\\)['\"]?[ \t]*\\(?:#.*\\)?\r?$")
            (setq found t done t)))
          (forward-line 1))
        found))))

(defun work-kb--visit-jira (file)
  "기계 소유 FILE(jira/KEY.md, wiki/_proposals.md)을 지금 창에 연다. 이미 연 버퍼가
디스크보다 낡았고 수정이 없으면 묻지 않고 다시 읽는다. 그 버퍼를 돌려준다."
  (let ((live (find-buffer-visiting file)))
    (when (and live
               (not (buffer-modified-p live))
               (not (verify-visited-file-modtime live)))
      (with-current-buffer live
        (let ((coding-system-for-read 'utf-8)) (revert-buffer t t t))))
    (if live
        (pop-to-buffer-same-window live)
      (let ((coding-system-for-read 'utf-8)) (find-file file)))
    (window-buffer (selected-window))))

(defun work-kb--key-fallback (key file why)
  "동기화 실패 뒤: FILE이 있으면 그대로 열고, 없으면 `kb.py link --stub KEY'로 만든 뒤 연다.
WHY는 실패 이유 (메시지에 넣는다)."
  (let ((sync-buf (work-kb--sync-buffer-name key)))
    (if (file-exists-p file)
        (progn
          (work-kb--visit-jira file)
          (message "work-kb: %s 동기화 실패 (%s). 저장된 페이지를 엽니다. %s 버퍼를 보세요"
                   key why sync-buf))
      (work-kb--run (work-kb--kb-args "link" "--stub" key)
                    :on-exit
                    (lambda (_proc code)
                      (if (file-exists-p file)
                          (progn
                            (work-kb--visit-jira file)
                            (message "work-kb: %s 동기화 실패 (%s). stub을 만들어 엽니다. %s 버퍼를 보세요"
                                     key why sync-buf))
                        (message "work-kb: %s 동기화 실패 (%s), stub 생성도 실패 (%s). %s, %s 버퍼를 보세요"
                                 key why (work-kb--exit-reason code) sync-buf work-kb-output-buffer)))))))

(defun work-kb--after-key-sync (key file code)
  "`kb.py sync --key KEY'가 CODE로 끝난 뒤: 받아 왔으면 열고, 아니면 `work-kb--key-fallback'."
  (setq work-kb--syncing-keys (delete key work-kb--syncing-keys))
  (let ((sync-buf (get-buffer (work-kb--sync-buffer-name key))))
    (cond
     ((and (eq code 0) (file-exists-p file) (not (work-kb--jira-stub-p file)))
      (when (buffer-live-p sync-buf) (kill-buffer sync-buf))
      (work-kb--visit-jira file)
      (message "work-kb: %s 동기화했습니다" key))
     (t
      (let ((last (work-kb--last-line sync-buf)))
        (work-kb--key-fallback
         key file
         (concat (if (eq code 0) "Jira에서 받아 오지 못함" (work-kb--exit-reason code))
                 (if last (concat " — " last) ""))))))))

(defun work-kb--open-key (key)
  "jira/KEY.md를 연다. 없거나 stub(`stub: true')이면 `kb.py sync --key KEY'를 비동기로
돌리고 센티널에서 연다. 동기화가 실패하면 stub(없으면 `kb.py link --stub KEY'로 만든다)을
열고 이유를 알린다 (D28). 바로 열었으면 그 버퍼, 동기화를 시작했으면 프로세스를 돌려준다."
  (let ((file (work-kb--jira-file key)))
    (cond
     ((and (file-exists-p file) (not (work-kb--jira-stub-p file)))
      (work-kb--visit-jira file))
     ((member key work-kb--syncing-keys)
      (message "work-kb: %s 동기화가 이미 진행 중입니다 (끝나면 열립니다)" key)
      nil)
     (t
      (push key work-kb--syncing-keys)
      (condition-case err
          (prog1 (work-kb--run (work-kb--kb-args "sync" "--key" key)
                               :buffer (work-kb--sync-buffer-name key)
                               :on-exit (lambda (_proc code) (work-kb--after-key-sync key file code)))
            (message "work-kb: %s 동기화 중… (끝나면 엽니다)" key))
        (error
         (setq work-kb--syncing-keys (delete key work-kb--syncing-keys))
         (signal (car err) (cdr err))))))))

(defun work-kb--resolve-link (target)
  "위키링크 대상의 파일 경로. 못 찾으면 nil."
  (let* ((vault (work-kb--vault))
         (has-ext (file-name-extension target))
         (name (if has-ext target (concat target ".md")))
         (dirs '("" "daily/" "wiki/" "wiki/projects/" "wiki/people/" "wiki/topics/" "jira/" "inbox/"))
         (found (cl-loop for dir in dirs
                         for path = (expand-file-name (concat dir name) vault)
                         when (file-regular-p path) return path)))
    (or found
        (let ((base (file-name-nondirectory name)))
          (cl-loop for dir in '("daily" "inbox" "wiki" "jira" "raw")
                   for full = (expand-file-name dir vault)
                   when (file-directory-p full)
                   thereis (car (directory-files-recursively
                                 full (concat "\\`" (regexp-quote base) "\\'"))))))))

(defun work-kb-open-at-point ()
  "커서 위치의 KEY-123 / [[…]] / @이름 페이지를 연다.
Jira 키는 캐시가 없거나 stub이면 먼저 `kb.py sync --key'로 받아 온다 (비동기)."
  (interactive)
  (let ((thing (work-kb--thing-at-point)))
    (pcase thing
      ('nil (user-error "커서 위치에 Jira 키·[[링크]]·@이름이 없습니다"))
      (`(key . ,key) (work-kb--open-key key))
      (`(person . ,name)
       (let ((file (expand-file-name (format "wiki/people/%s.md" (work-kb--safe-name name))
                                     (work-kb--vault))))
         (if (file-exists-p file)
             (find-file file)
           (user-error "@%s 페이지가 아직 없습니다. l(link)을 실행하세요" name))))
      (`(link . ,target)
       (let ((file (work-kb--resolve-link target)))
         (if file (find-file file) (user-error "페이지가 없습니다: %s" target)))))))

(defun work-kb-link ()
  "`kb.py link'를 실행하고 요약을 보여 준다."
  (interactive)
  (work-kb--run (work-kb--kb-args "link")
                :on-exit (lambda (proc code)
                           (let ((buf (process-buffer proc)))
                             (when (buffer-live-p buf) (display-buffer buf))
                             (if (eq code 0)
                                 (message "work-kb link: %s" (or (work-kb--last-line buf) "완료"))
                               (message "work-kb link 실패 (종료 %s)" code))))))

(defun work-kb-status ()
  "`kb.py status' 결과를 *work-kb* 버퍼에 보인다."
  (interactive)
  (work-kb--run (work-kb--kb-args "status")
                :on-exit (lambda (proc code)
                           (let ((buf (process-buffer proc)))
                             (when (buffer-live-p buf) (display-buffer buf))
                             (unless (eq code 0)
                               (message "work-kb status 실패 (종료 %s)" code))))))

;;;; Jira 작업 (Phase 3, `j' transient, §6.8)

;; 쓰기는 모두 `kb.py act <하위 명령>'(outbox 경유, D52)로 보낸다. Emacs는 Jira에 직접 붙지 않는다.
;; 상태 전환은 사용자가 고르고 y-or-n-p로 확인한 뒤에만 `--yes'로 보낸다 (§5.1, §10: 자동 전환 없음).

(require 'transient)

(defcustom work-kb-jira-issue-types '("Task" "Bug" "Story")
  "`n'(새 일감)에서 고를 일감 유형 후보. 목록에 없는 이름도 입력할 수 있다."
  :type '(repeat string)
  :group 'work-kb)

(defconst work-kb-jira-buffer "*work-kb-jira*"
  "`kb.py act' 출력 버퍼.")

(defvar work-kb--jql-history nil)
(defvar work-kb--assignee-history nil)

(defun work-kb--act-args (sub &rest args)
  "kb.py 인수 목록: act SUB --root <볼트> ARGS...
하위 명령은 `act' 바로 뒤에 둔다 (kb.py가 argv[1]로 `act list'를 가른다)."
  (append (list "act" sub "--root" (directory-file-name (work-kb--vault))) args))

(defun work-kb--key-on-line ()
  "현재 줄의 첫 Jira 키 (앞뒤가 키 글자에 붙어 있으면 키가 아니다). 없으면 nil."
  (save-excursion
    (let ((bol (line-beginning-position))
          (eol (line-end-position))
          (case-fold-search nil)
          found)
      (goto-char bol)
      (while (and (not found) (re-search-forward work-kb--key-regexp eol t))
        (when (work-kb--key-bounded-p (match-beginning 0) (match-end 0) bol eol)
          (setq found (match-string-no-properties 0))))
      found)))

(defun work-kb--key-at-point ()
  "작업할 Jira 키. 없으면 nil.
커서 위치의 키·키 링크 → 현재 줄의 첫 키 → 지금 연 jira/KEY.md의 키.
커서가 키가 아닌 링크([[KB-12-설계]]·[[projects/KB]]) 위면
줄의 다른 키로 넘어가지 않고 nil이다 (엉뚱한 일감에 보내지 않게 묻는다)."
  (pcase (work-kb--thing-at-point)
    (`(key . ,key) key)
    (`(link . ,target) (work-kb--link-key target))
    (_
     (or (work-kb--key-on-line)
         (let ((dir (or work-kb-vault work-kb-root)))
           (and buffer-file-name (stringp dir)
                (string-prefix-p (file-name-as-directory (expand-file-name "jira" dir))
                                 (expand-file-name buffer-file-name) t)
                (let ((base (file-name-base buffer-file-name))
                      (case-fold-search nil))
                  (and (string-match-p (concat "\\`" work-kb--key-regexp "\\'") base) base))))))))

(defun work-kb--jira-key ()
  "커서 위치의 Jira 키. 없으면 묻는다 (소문자는 대문자로)."
  (or (work-kb--key-at-point)
      (let ((key (upcase (string-trim (read-string "Jira 키: "))))
            (case-fold-search nil))
        (unless (string-match-p (concat "\\`" work-kb--key-regexp "\\'") key)
          (user-error "Jira 키가 아닙니다: %S (예: KB-12)" key))
        key)))

(defun work-kb--current-line-text ()
  (string-trim (buffer-substring-no-properties (line-beginning-position) (line-end-position))))

(defun work-kb--daily-relative (file)
  "FILE이 볼트 daily/ 아래 .md면 `daily/<이름>.md', 아니면 nil."
  (let ((dir (or work-kb-vault work-kb-root)))
    (when (and (stringp file) (stringp dir)
               (string-match-p "\\.[mM][dD]\\'" file))
      (let ((daily (file-name-as-directory (expand-file-name "daily" dir)))
            (full (expand-file-name file)))
        (when (string-prefix-p daily full t)
          (concat "daily/" (substring full (length daily))))))))

(defun work-kb--daily-line-ref ()
  "현재 버퍼가 볼트의 daily 파일이면 `daily/<이름>.md:<줄 번호>', 아니면 nil.
Python은 디스크의 줄을 읽으므로 저장 안 된 수정이 있으면 먼저 저장한다 (캡처와 같은 규칙, D26).
버퍼와 파일이 모두 바뀌었으면 사용자 오류."
  (let ((rel (work-kb--daily-relative buffer-file-name)))
    (when rel
      (when (buffer-modified-p)
        (when (and (file-exists-p buffer-file-name)
                   (not (verify-visited-file-modtime (current-buffer))))
          (user-error "%s: 버퍼와 파일이 모두 바뀌었습니다. 먼저 정리하세요" (buffer-name)))
        (let ((before-save-hook nil)
              (after-save-hook nil)
              (require-final-newline nil)
              (backup-inhibited t)
              (save-silently t))
          (save-buffer)))
      (format "%s:%d" rel (line-number-at-pos nil t)))))

(defun work-kb--parse-json-output (text)
  "kb.py 출력 TEXT에서 JSON 값(배열·객체) 하나를 읽는다. 앞의 로그 줄은 건너뛴다.
객체는 alist, 배열은 리스트. 못 읽으면 nil."
  (with-temp-buffer
    (insert (or text ""))
    (goto-char (point-min))
    (let (result)
      (while (and (not result) (re-search-forward "^[ \t]*[[{]" nil t))
        (let ((start (line-beginning-position)))
          (goto-char (1- (match-end 0)))
          (setq result (condition-case nil
                           (list (json-parse-buffer :object-type 'alist :array-type 'list
                                                    :null-object nil :false-object nil))
                         (error (goto-char start) (forward-line 1) nil)))))
      (car result))))

(defun work-kb--alist-p (data)
  "DATA가 JSON 객체(alist)인가."
  (and (consp data) (consp (car data)) (symbolp (caar data))))

(defun work-kb--json-text (value)
  "JSON 값 VALUE를 비어 있지 않은 문자열로 (숫자는 글자로). 아니면 nil."
  (let ((text (cond ((stringp value) value)
                    ((integerp value) (number-to-string value)))))
    (and text (not (string-empty-p (string-trim text))) (string-trim text))))

(defun work-kb--transition-choices (data)
  "`act transitions KEY --json' 결과 DATA의 전환 후보 alist.
((표시 . 보낼 값) …), 순서 유지. 전환마다 `이름 → 도착 (id)' 한 줄이고
보낼 값은 id다. 이름이 같은 전환(Done→Done, Done→Closed)도 따로 고르고,
고른 그 전환이 간다. id가 없으면 이름을 보낸다.
배열(객체 또는 문자열)이나 {\"transitions\": 배열} 둘 다 받는다."
  (let ((items (if (work-kb--alist-p data) (alist-get 'transitions data) data))
        choices)
    (dolist (item (and (listp items) items))
      (cond
       ((work-kb--json-text item)
        (push (cons (work-kb--json-text item) (work-kb--json-text item)) choices))
       ((work-kb--alist-p item)
        (let* ((name (work-kb--json-text (alist-get 'name item)))
               (id (work-kb--json-text (alist-get 'id item)))
               (to-raw (alist-get 'to item))
               (to (work-kb--json-text (if (work-kb--alist-p to-raw) (alist-get 'name to-raw) to-raw))))
          (when name
            (push (cons (concat name (if to (format " → %s" to) "") (if id (format " (%s)" id) ""))
                        (or id name))
                  choices))))))
    ;; 같은 표시가 겹치면(같은 id가 두 번) 처음 것만 둔다.
    (cl-remove-duplicates (nreverse choices) :key #'car :test #'equal :from-end t)))

(defun work-kb--find-project-keys (data &optional depth)
  "status JSON DATA 어디든 있는 `project_keys' 목록 (문자열만). 없으면 nil."
  (let ((depth (or depth 0)))
    (when (and (work-kb--alist-p data) (< depth 4))
      (let ((direct (alist-get 'project_keys data)))
        (if (and (listp direct) direct (cl-every #'stringp direct))
            direct
          (cl-loop for (_k . v) in data
                   thereis (work-kb--find-project-keys v (1+ depth))))))))

(defun work-kb--run-output (args buffer &optional timeout)
  "ARGS로 kb.py를 실행하고 끝날 때까지 기다린다. (종료코드 . BUFFER 내용).
센티널에서 사용자에게 묻지 않으려고(C-g가 먹도록) 목록 조회는 이것으로 기다린다."
  (let* ((code 'pending)
         (proc (work-kb--run args :buffer buffer :on-exit (lambda (_proc c) (setq code c))))
         (deadline (+ (float-time) (or timeout work-kb-process-timeout))))
    (while (and (eq code 'pending) (< (float-time) deadline))
      (accept-process-output (and (processp proc) (process-live-p proc) proc) 0.05))
    (when (eq code 'pending)
      (when (and (processp proc) (process-live-p proc)) (delete-process proc))
      (user-error "work-kb: %s 응답이 없습니다 (%s초)" (car args) (or timeout work-kb-process-timeout)))
    (cons code (with-current-buffer (get-buffer-create buffer)
                 (buffer-substring-no-properties (point-min) (point-max))))))

(defun work-kb--act-message (what code)
  "act WHAT이 CODE로 끝난 뒤 알린다. 실패면 출력 버퍼를 보인다."
  (let* ((buf (get-buffer work-kb-jira-buffer))
         (last (work-kb--last-line buf)))
    (unless (eq code 0)
      (when (buffer-live-p buf) (display-buffer buf)))
    (pcase code
      (0 (message "work-kb: %s 완료%s" what (if last (concat " — " last) "")))
      (3 (message "work-kb: %s — 네트워크 오류로 outbox에 남겼습니다. 나중에 f(flush)로 보냅니다" what))
      (2 (message "work-kb: %s — 인증 실패 (outbox에 남김). 토큰을 확인하세요: kb.py init" what))
      (_ (message "work-kb: %s 실패 (%s). %s 버퍼를 보세요" what (work-kb--exit-reason code) work-kb-jira-buffer)))))

(defun work-kb--act-run (what args &optional after)
  "ARGS(`kb.py act …')를 비동기로 보내고 끝나면 WHAT으로 알린다. AFTER가 있으면 (AFTER 종료코드)."
  (work-kb--run args
                :buffer work-kb-jira-buffer
                :on-exit (lambda (_proc code)
                           (work-kb--act-message what code)
                           (when after (funcall after code)))))

(defun work-kb--read-project ()
  "프로젝트 키를 고른다. 후보는 `kb.py status --json'의 project_keys (없으면 직접 입력)."
  (let* ((out (work-kb--run-output (work-kb--kb-args "status" "--json") "*work-kb-status-json*"))
         (keys (and (eq (car out) 0) (work-kb--find-project-keys (work-kb--parse-json-output (cdr out)))))
         (project (upcase (string-trim (completing-read (format-prompt "프로젝트" (car keys))
                                                        keys nil nil nil nil (car keys)))))
         (case-fold-search nil))
    (unless (string-match-p "\\`[A-Z][A-Z0-9_]+\\'" project)
      (user-error "프로젝트 키가 아닙니다: %S (예: KB)" project))
    project))

(defun work-kb--read-nonempty (prompt &optional initial)
  (let ((text (string-trim (read-string prompt initial))))
    (when (string-empty-p text) (user-error "비어 있어 취소했습니다"))
    text))

(defun work-kb-jira-view ()
  "v: 커서 위치(또는 입력한) 키의 jira/KEY.md를 연다. 없거나 stub이면 `o'와 같이 먼저 동기화."
  (interactive)
  (work-kb--open-key (work-kb--jira-key)))

(defun work-kb-jira-comment ()
  "c: 댓글. 기본 본문은 현재 줄. daily에서 본문을 그대로 두면 `--from-line daily/…:N'(출처 기록),
고쳤거나 daily가 아니면 `--body'로 보낸다."
  (interactive)
  (let* ((key (work-kb--jira-key))
         (line (work-kb--current-line-text))
         (daily (work-kb--daily-relative buffer-file-name))
         (body (string-trim (read-string (format "%s 댓글: " key) line))))
    (when (string-empty-p body) (user-error "빈 댓글은 보내지 않습니다"))
    (work-kb--act-run (format "%s 댓글" key)
                      (if (and daily (equal body line))
                          (work-kb--act-args "comment" key "--from-line" (work-kb--daily-line-ref))
                        (work-kb--act-args "comment" key "--body" body)))))

(defun work-kb-jira-transition ()
  "t: 전환. `act transitions KEY --json'으로 후보를 받아 고르고, 확인하면 `--yes'로 보낸다."
  (interactive)
  (let* ((key (work-kb--jira-key))
         (out (work-kb--run-output (work-kb--act-args "transitions" key "--json") work-kb-jira-buffer)))
    (unless (eq (car out) 0)
      (display-buffer work-kb-jira-buffer)
      (user-error "work-kb: %s 전환 목록을 받지 못했습니다 (%s)" key (work-kb--exit-reason (car out))))
    (let ((choices (work-kb--transition-choices (work-kb--parse-json-output (cdr out)))))
      (unless choices (user-error "work-kb: %s에서 할 수 있는 전환이 없습니다" key))
      (let* ((label (completing-read (format "%s 전환: " key) choices nil t))
             (choice (assoc label choices)))
        (unless choice (user-error "전환을 고르지 않았습니다"))
        (if (y-or-n-p (format "%s을(를) '%s'(으)로 전환할까요? " key label))
            (work-kb--act-run (format "%s 전환(%s)" key label)
                              (work-kb--act-args "transition" key (cdr choice) "--yes"))
          (message "work-kb: 전환을 취소했습니다")
          nil)))))

(defun work-kb-jira-assign ()
  "a: 담당자 지정 (Jira 사용자명)."
  (interactive)
  (let* ((key (work-kb--jira-key))
         (user (string-trim (read-string (format "%s 담당자 (Jira 사용자명): " key)
                                         nil 'work-kb--assignee-history))))
    (when (string-empty-p user) (user-error "담당자가 비었습니다"))
    (work-kb--act-run (format "%s 담당 %s" key user) (work-kb--act-args "assign" key user))))

(defconst work-kb--duration-regexp "\\`\\(?:[0-9]+h\\)?\\(?:[0-9]+m\\)?\\'"
  "워크로그 시간: XhYm·Xh·Ym (jira_act.parse_duration과 같다).
일·주는 받지 않는다. 빈 문자열도 맞으므로 따로 거른다.")

(defun work-kb-jira-worklog ()
  "w: 워크로그. 시간(예: 1h30m)과 설명(선택)."
  (interactive)
  (let* ((key (work-kb--jira-key))
         (spent (downcase (replace-regexp-in-string
                           "[ \t]+" "" (read-string (format "%s 작업 시간 (예: 1h30m): " key)) t t))))
    (unless (and (not (string-empty-p spent)) (string-match-p work-kb--duration-regexp spent))
      (user-error "시간 형식이 아닙니다: %S (예: 1h30m, 2h, 45m)" spent))
    (unless (string-match-p "[1-9]" spent)
      (user-error "작업 시간은 1분 이상이어야 합니다: %S" spent))
    (let ((comment (string-trim (read-string (format "%s 워크로그 설명 (선택): " key)))))
      (work-kb--act-run (format "%s 워크로그 %s" key spent)
                        (apply #'work-kb--act-args "worklog" key spent
                               (unless (string-empty-p comment) (list "--comment" comment)))))))

(defun work-kb-jira-create ()
  "n: 새 일감. 프로젝트·요약·유형·설명을 받고 확인하면 `--yes'로 보낸다."
  (interactive)
  (let* ((project (work-kb--read-project))
         (summary (work-kb--read-nonempty (format "%s 새 일감 요약: " project)))
         (type (string-trim (completing-read (format-prompt "유형" (car work-kb-jira-issue-types))
                                             work-kb-jira-issue-types nil nil nil nil
                                             (car work-kb-jira-issue-types))))
         (description (string-trim (read-string "설명 (선택): "))))
    (when (string-empty-p type) (user-error "유형이 비었습니다"))
    (if (y-or-n-p (format "%s에 %s 일감 '%s'을(를) 만들까요? " project type summary))
        (work-kb--act-run (format "%s 새 일감" project)
                          (apply #'work-kb--act-args "create" project "--summary" summary "--type" type
                                 (append (unless (string-empty-p description) (list "--description" description))
                                         (list "--yes"))))
      (message "work-kb: 새 일감을 취소했습니다")
      nil)))

(defun work-kb--revert-if-stale (buffer)
  "BUFFER가 수정 없이 디스크보다 낡았으면 묻지 않고 다시 읽는다."
  (when (and (buffer-live-p buffer)
             (buffer-file-name buffer)
             (not (buffer-modified-p buffer))
             (not (verify-visited-file-modtime buffer)))
    (with-current-buffer buffer
      (let ((coding-system-for-read 'utf-8)) (revert-buffer t t t)))))

(defun work-kb-jira-promote ()
  "p: 현재 daily 줄을 새 일감으로 승격 (`act promote --from daily/…:N PROJECT --yes').
성공하면 kb.py가 그 줄 끝에 ` [[NEW-1]]'을 붙인다 (D53). 줄이 바뀌었으면 kb.py가 거부한다."
  (interactive)
  (unless (work-kb--daily-relative buffer-file-name)
    (user-error "승격은 daily 파일의 줄에서만 됩니다"))
  (let ((line (work-kb--current-line-text))
        (buffer (current-buffer)))
    (when (string-empty-p line) (user-error "빈 줄은 승격할 수 없습니다"))
    (let ((project (work-kb--read-project)))
      (if (y-or-n-p (format "이 줄을 %s 일감으로 만들까요? %s " project line))
          (work-kb--act-run (format "%s 승격" project)
                            (work-kb--act-args "promote" "--from" (work-kb--daily-line-ref) project "--yes")
                            (lambda (code) (when (eq code 0) (work-kb--revert-if-stale buffer))))
        (message "work-kb: 승격을 취소했습니다")
        nil))))

(defvar work-kb-results-mode-map
  (let ((map (make-sparse-keymap)))
    (keymap-set map "RET" #'work-kb-open-at-point)
    map))

(define-derived-mode work-kb-results-mode special-mode "work-kb 결과"
  "kb.py 결과 버퍼 (읽기 전용). RET: 커서 위치의 키 열기.")

(defun work-kb--show-results (buffer what code)
  (let ((buf (get-buffer buffer)))
    (when (buffer-live-p buf)
      (with-current-buffer buf
        (work-kb-results-mode)
        (goto-char (point-min)))
      (display-buffer buf))
    (if (eq code 0)
        (message "work-kb: %s 완료" what)
      (message "work-kb: %s 실패 (%s)" what (work-kb--exit-reason code)))))

(defun work-kb-jira-jql (jql)
  "q: 라이브 JQL (`kb.py sync --jql', 캐시에 쓰지 않음) 결과를 *work-kb-jql* 버퍼에 보인다."
  (interactive (list (read-string "JQL: " nil 'work-kb--jql-history)))
  (let ((jql (string-trim (or jql ""))))
    (when (string-empty-p jql) (user-error "JQL이 비었습니다"))
    (work-kb--run (work-kb--kb-args "sync" "--jql" jql)
                  :buffer "*work-kb-jql*"
                  :on-exit (lambda (_proc code) (work-kb--show-results "*work-kb-jql*" "라이브 JQL" code)))))

(defun work-kb-jira-sync ()
  "S: 동기화 (`kb.py sync')."
  (interactive)
  (message "work-kb: 동기화 중…")
  (work-kb--run (work-kb--kb-args "sync")
                :buffer "*work-kb-sync*"
                :on-exit (lambda (_proc code)
                           (let ((buf (get-buffer "*work-kb-sync*")))
                             (if (eq code 0)
                                 (message "work-kb: 동기화 완료%s"
                                          (let ((last (work-kb--last-line buf))) (if last (concat " — " last) "")))
                               (when (buffer-live-p buf) (display-buffer buf))
                               (message "work-kb: 동기화 실패 (%s). *work-kb-sync* 버퍼를 보세요"
                                        (work-kb--exit-reason code)))))))

(defun work-kb-jira-flush ()
  "f: outbox의 대기 항목을 다시 보낸다 (`kb.py act flush')."
  (interactive)
  (work-kb--act-run "outbox 다시 보내기" (work-kb--act-args "flush")))

(defun work-kb-jira-check-done ()
  "d: 완료 전환 제안 (`kb.py today --check-done'). 제안만 보이고 아무것도 바꾸지 않는다."
  (interactive)
  (work-kb--run (work-kb--kb-args "today" "--check-done")
                :buffer "*work-kb-check-done*"
                :on-exit (lambda (_proc code) (work-kb--show-results "*work-kb-check-done*" "완료 전환 제안" code))))

(transient-define-prefix work-kb-jira ()
  "Jira 작업 메뉴 (§6.8). 쓰기는 모두 `kb.py act'(outbox 경유)로 보낸다."
  [["일감"
    ("v" "보기" work-kb-jira-view)
    ("c" "댓글" work-kb-jira-comment)
    ("t" "전환" work-kb-jira-transition)
    ("a" "담당" work-kb-jira-assign)
    ("w" "워크로그" work-kb-jira-worklog)]
   ["만들기"
    ("n" "새 일감" work-kb-jira-create)
    ("p" "현재 줄 승격" work-kb-jira-promote)]
   ["조회·동기화"
    ("q" "라이브 JQL" work-kb-jira-jql)
    ("S" "동기화" work-kb-jira-sync)
    ("f" "outbox 다시 보내기" work-kb-jira-flush)
    ("d" "완료 전환 제안" work-kb-jira-check-done)]])

;;;; 편찬·주제 제안 (Phase 4, `g'·`a', §6.8)

;; `g'와 `a'도 `work-kb--run'(stdin JSON)만 거친다. LLM 연결·회사 가드는 모두 Python 쪽 일이다.

(defvar work-kb--compile-running nil
  "`g'(ingest → compile)가 도는 중이면 t (두 번 돌려 *work-kb* 버퍼를 섞지 않는다).")

(defconst work-kb-proposals-file "wiki/_proposals.md"
  "주제 제안 파일 (볼트 기준).")

(defun work-kb--step-result (name code)
  "단계 NAME이 CODE로 끝난 결과 문구."
  (if (eq code 0)
      (format "%s 완료" name)
    (format "%s 실패(%s)" name (work-kb--exit-reason code))))

(defun work-kb--finish-compile (label ingest-code compile-code)
  "`g'가 끝났다: 버퍼를 보이고 두 단계 결과를 알린다. COMPILE-CODE가 nil이면 compile을 건너뛰었다."
  (setq work-kb--compile-running nil)
  (let ((buf (get-buffer work-kb-output-buffer)))
    (when (buffer-live-p buf) (display-buffer buf))
    (cond
     ((and (eq ingest-code 0) (eq compile-code 0))
      (message "work-kb: %s 완료%s" label
               (let ((last (work-kb--last-line buf))) (if last (concat " — " last) ""))))
     ((null compile-code)
      (message "work-kb: %s 중단 — %s. compile은 돌리지 않았습니다. %s 버퍼를 보세요"
               label (work-kb--step-result "ingest" ingest-code) work-kb-output-buffer))
     (t
      (message "work-kb: %s 끝 — %s, %s. %s 버퍼를 보세요"
               label (work-kb--step-result "ingest" ingest-code)
               (work-kb--step-result "compile" compile-code) work-kb-output-buffer)))))

(defun work-kb--after-ingest (ingest-code extra label)
  "ingest가 INGEST-CODE로 끝난 뒤 compile을 같은 버퍼에 이어 돌린다.
공개 호스트 거부(종료 5)면 compile도 같은 가드에 걸리므로 돌리지 않는다."
  (if (eq ingest-code 5)
      (work-kb--finish-compile label ingest-code nil)
    (condition-case err
        (work-kb--run (apply #'work-kb--kb-args "compile" extra)
                      :buffer work-kb-output-buffer
                      :append t
                      :header (format "\n== compile%s ==\n" (if extra " --dry-run" ""))
                      :on-exit (lambda (_proc code) (work-kb--finish-compile label ingest-code code)))
      (error
       (setq work-kb--compile-running nil)
       (message "work-kb: compile을 시작하지 못했습니다: %s" (error-message-string err))))))

(defun work-kb-ingest (&optional dry-run)
  "g: 이미지 추출(`kb.py ingest') 뒤 편찬(`kb.py compile')을 차례로 돌린다.
두 출력은 한 *work-kb* 버퍼에 이어 쓴다. DRY-RUN(C-u)이면 둘 다 `--dry-run'(쓰지 않고 diff만).
ingest가 실패해도(공개 호스트 거부 제외) compile은 돌린다: 글 편찬은 이미지와 따로 쓸모가 있다.
이미 도는 중이면 다시 돌리지 않는다. 프로세스를 돌려준다 (도는 중이면 nil)."
  (interactive "P")
  (if work-kb--compile-running
      (progn (message "work-kb: 편찬이 이미 진행 중입니다 (%s 버퍼)" work-kb-output-buffer)
             nil)
    (let ((extra (and dry-run (list "--dry-run")))
          (label (if dry-run "편찬 미리 보기" "편찬")))
      (setq work-kb--compile-running t)
      (message "work-kb: %s 중… (ingest → compile)" label)
      (condition-case err
          (work-kb--run (apply #'work-kb--kb-args "ingest" extra)
                        :buffer work-kb-output-buffer
                        :header (format "== ingest%s ==\n" (if extra " --dry-run" ""))
                        :on-exit (lambda (_proc code) (work-kb--after-ingest code extra label)))
        (error
         (setq work-kb--compile-running nil)
         (signal (car err) (cdr err)))))))

(defun work-kb--proposals-args (sub &rest args)
  "kb.py 인수 목록: proposals SUB --root <볼트> ARGS...
하위 명령은 `proposals' 바로 뒤에 둔다 (kb.py가 argv[1]로 `proposals list'를 가른다)."
  (append (list "proposals" sub "--root" (directory-file-name (work-kb--vault))) args))

(defun work-kb--read-proposal-choice ()
  "승인할 제안: 번호 문자열(앞의 #은 뗀다), \"all\", 비우면 nil. 그 밖은 사용자 오류."
  (let ((text (downcase (string-trim (read-string "승인할 제안 번호 (all = 전부, 비우면 보기만): ")))))
    (cond
     ((string-empty-p text) nil)
     ((member text '("all" "전부" "모두")) "all")
     ((string-match "\\`#?\\([0-9]+\\)\\'" text) (match-string 1 text))
     (t (user-error "제안 번호나 all이 아닙니다: %S" text)))))

(defun work-kb-proposals ()
  "a: wiki/_proposals.md를 열고, 번호(또는 all)를 받아 확인(y-or-n-p)하면
`kb.py proposals accept <n|all>'을 보낸다 (승인은 wiki/topics/에 주제 페이지를 만들거나
merge_with 주제에 별칭을 더한다, D22). 비우면 열기만 한다. 끝나면 제안 버퍼를 다시 읽는다.
파일이 없으면 묻지 않는다. 승인을 보냈으면 프로세스, 아니면 nil."
  (interactive)
  (let ((file (expand-file-name work-kb-proposals-file (work-kb--vault))))
    (if (not (file-exists-p file))
        (progn (message "work-kb: %s이 아직 없습니다. g(편찬)로 주제 제안을 만드세요" work-kb-proposals-file)
               nil)
      (let* ((buffer (work-kb--visit-jira file))
             (choice (work-kb--read-proposal-choice))
             (what (if (equal choice "all") "모든 제안" (format "제안 %s" choice))))
        (cond
         ((null choice)
          (message "work-kb: 승인하지 않았습니다 (보기만)")
          nil)
         ((not (y-or-n-p (format "%s을(를) 승인할까요? (wiki/topics/에 주제 페이지를 만들거나 별칭을 더합니다) "
                                 what)))
          (message "work-kb: 승인을 취소했습니다")
          nil)
         (t
          (work-kb--run (work-kb--proposals-args "accept" choice)
                        :buffer work-kb-output-buffer
                        :on-exit
                        (lambda (_proc code)
                          (work-kb--revert-if-stale buffer)
                          (let ((buf (get-buffer work-kb-output-buffer)))
                            (if (eq code 0)
                                (message "work-kb: %s 승인 완료%s" what
                                         (let ((last (work-kb--last-line buf))) (if last (concat " — " last) "")))
                              (when (buffer-live-p buf) (display-buffer buf))
                              (message "work-kb: %s 승인 실패 (%s). %s 버퍼를 보세요"
                                       what (work-kb--exit-reason code) work-kb-output-buffer)))))))))))

;;;; 볼트 버퍼 (D11)

(defun work-kb--find-file-hook ()
  "볼트 파일: 새 파일은 utf-8-unix, 자동 되읽기, 위키링크 강조."
  (when (and buffer-file-name (work-kb--vault-file-p buffer-file-name))
    (unless (file-exists-p buffer-file-name)
      (setq buffer-file-coding-system 'utf-8-unix))
    (auto-revert-mode 1)
    (when (boundp 'markdown-enable-wiki-links)
      (setq-local markdown-enable-wiki-links t))))

(add-hook 'find-file-hook #'work-kb--find-file-hook)

(work-kb--register-coding)
(work-kb-rebind-prefix work-kb-prefix-key)

(provide 'work-kb)

;;; work-kb.el ends here
