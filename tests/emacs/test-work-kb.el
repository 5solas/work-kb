;;; test-work-kb.el --- work-kb.el ERT -*- lexical-binding: t; coding: utf-8 -*-

;; 실행: emacs -Q --batch --eval "(set-language-environment \"Korean\")" \
;;         -l tests/emacs/test-work-kb.el -f ert-run-tests-batch-and-exit
;; tests/test_emacs_ert.py가 콘솔 상속·CREATE_NO_WINDOW(cp949) 두 가지로 돌린다.
;; 환경변수: WORK_KB_TEST_TMP (ASCII 임시 폴더, 없으면 새로 만듦),
;;           WORK_KB_TEST_EXPECT_KOREAN=1 (한국어 언어 환경이 켜졌는지 확인).

;;; Code:

(require 'ert)
(require 'cl-lib)
(require 'subr-x)

(defconst work-kb-test--dir
  (file-name-directory (or load-file-name buffer-file-name)))
(defconst work-kb-test--repo
  (file-name-as-directory (expand-file-name "../.." work-kb-test--dir)))
(defconst work-kb-test--el (expand-file-name "emacs/work-kb.el" work-kb-test--repo))

(load work-kb-test--el nil t)

(defconst work-kb-test--tmp
  (file-name-as-directory
   (or (getenv "WORK_KB_TEST_TMP") (make-temp-file "work-kb-ert-" t))))

(defconst work-kb-test--time (encode-time (list 0 12 9 5 11 2026 nil -1 nil))
  "2026-11-05 09:12:00 (로컬 시간).")

(defvar work-kb-test--counter 0)

;;;; 도우미

(defun work-kb-test--make-vault (&optional label)
  "한글·공백 경로의 빈 볼트를 만든다 (끝에 /)."
  (let ((dir (expand-file-name (format "%s %d" (or label "한글 볼트") (cl-incf work-kb-test--counter))
                               work-kb-test--tmp)))
    (dolist (sub '("daily" "inbox" "raw/assets" "jira/_views" "wiki/people" "wiki/projects" "wiki/topics" ".state"))
      (make-directory (expand-file-name sub dir) t))
    (file-name-as-directory dir)))

(defun work-kb-test--kill-buffers-under (dir)
  (dolist (buf (buffer-list))
    (let ((file (buffer-file-name buf)))
      (when (and file (string-prefix-p (expand-file-name dir) (expand-file-name file) t))
        (with-current-buffer buf (set-buffer-modified-p nil))
        (kill-buffer buf)))))

(defmacro work-kb-test--with-vault (var &rest body)
  "VAR에 새 볼트를 묶고, 루트는 저장소, 시각은 고정해서 BODY를 실행한다."
  (declare (indent 1))
  `(let* ((,var (work-kb-test--make-vault))
          (work-kb-vault ,var)
          (work-kb-root work-kb-test--repo)
          (work-kb--now-function (lambda () work-kb-test--time)))
     (unwind-protect (progn ,@body)
       (work-kb-test--kill-buffers-under ,var))))

(defun work-kb-test--write-bytes (file text)
  "TEXT를 UTF-8 바이트 그대로 FILE에 쓴다 (EOL 변환 없음)."
  (make-directory (file-name-directory file) t)
  (let ((coding-system-for-write 'no-conversion))
    (write-region (encode-coding-string text 'utf-8-unix) nil file nil 'silent)))

(defun work-kb-test--bytes (file)
  (with-temp-buffer
    (set-buffer-multibyte nil)
    (insert-file-contents-literally file)
    (buffer-string)))

(defun work-kb-test--utf8 (text) (encode-coding-string text 'utf-8-unix))

(defun work-kb-test--ascii-p (text) (string-match-p "\\`[[:ascii:]]*\\'" text))

(defmacro work-kb-test--capturing-messages (var &rest body)
  (declare (indent 1))
  `(let ((,var nil))
     (cl-letf (((symbol-function 'message)
                (lambda (fmt &rest args)
                  (when fmt (push (apply #'format-message fmt args) ,var))
                  nil)))
       ,@body)))

(defun work-kb-test--python ()
  "저장소 .venv의 Python (상대 경로; work-kb--run이 루트를 exec-path에 넣는다)."
  (let ((work-kb-root work-kb-test--repo) (work-kb-python nil))
    (work-kb--python-command)))

(defconst work-kb-test--daily-lf
  (concat "---\ndate: 2026-11-05\n---\n## 오늘\n<!-- kb:today:start -->\n<!-- kb:today:end -->\n\n"
          "## 기록\n- 08:00 첫 기록\n\n## 메모\n자유 텍스트\n"))

;;;; 환경

(ert-deftest work-kb-test-korean-environment ()
  "러너가 한국어 언어 환경을 켰는지 (기본 파일 인코딩이 UTF-8이 아님)."
  (skip-unless (getenv "WORK_KB_TEST_EXPECT_KOREAN"))
  (should (equal current-language-environment "Korean"))
  (should-not (eq (coding-system-base (default-value 'buffer-file-coding-system)) 'utf-8)))

(ert-deftest work-kb-test-vault-coding ()
  "볼트 .md는 한국어 환경에서도 UTF-8로 읽히고, 새 파일은 utf-8-unix (D11)."
  (work-kb-test--with-vault vault
    (unwind-protect
        (progn
          (work-kb--register-coding)
          (let ((file (expand-file-name "daily/2026-11-04.md" vault))
                (new (expand-file-name "inbox/새 메모.md" vault)))
            (work-kb-test--write-bytes file "## 기록\n- 09:00 한글 😀\n")
            (with-current-buffer (find-file-noselect file)
              (should (string-match-p "한글 😀" (buffer-string)))
              (should (eq (coding-system-base buffer-file-coding-system) 'utf-8))
              (should auto-revert-mode))
            (with-current-buffer (find-file-noselect new)
              (should (eq buffer-file-coding-system 'utf-8-unix)))))
      (let ((work-kb-vault nil)) (work-kb--register-coding)))))

;;;; 키

(ert-deftest work-kb-test-every-key-bound ()
  (dolist (entry '(("c" . work-kb-capture) ("C" . work-kb-capture-clipboard-image)
                   ("F" . work-kb-capture-image-file) ("i" . work-kb-capture-inbox)
                   ("t" . work-kb-today) ("s" . work-kb-search) ("o" . work-kb-open-at-point)
                   ("l" . work-kb-link) ("?" . work-kb-status) ("j" . work-kb-jira)
                   ("g" . work-kb-ingest) ("a" . work-kb-proposals)))
    (should (eq (keymap-lookup work-kb-command-map (car entry)) (cdr entry)))
    (should (commandp (cdr entry))))
  (should (eq (keymap-lookup global-map "C-c k c") 'work-kb-capture))
  (should (eq (keymap-lookup global-map "C-c k ?") 'work-kb-status)))

(ert-deftest work-kb-test-later-phase-stubs ()
  (work-kb-test--capturing-messages msgs
    (work-kb-jira) (work-kb-ingest) (work-kb-proposals)
    (should (cl-some (lambda (m) (string-match-p "Phase 3" m)) msgs))
    (should (= 2 (cl-count-if (lambda (m) (string-match-p "Phase 4" m)) msgs)))))

(ert-deftest work-kb-test-prefix-rebind ()
  (unwind-protect
      (progn
        (should (eq (keymap-lookup global-map "C-c k") work-kb-command-map))
        (customize-set-variable 'work-kb-prefix-key "C-c c")
        (should (eq (keymap-lookup global-map "C-c c") work-kb-command-map))
        (should (eq (keymap-lookup global-map "C-c c c") 'work-kb-capture))
        (should-not (keymap-lookup global-map "C-c k"))
        (customize-set-variable 'work-kb-prefix-key "C-c k")
        (should (eq (keymap-lookup global-map "C-c k") work-kb-command-map))
        (should-not (keymap-lookup global-map "C-c c"))
        ;; setq 한 줄 + 다시 load
        (setq work-kb-prefix-key "C-c c")
        (load work-kb-test--el nil t)
        (should (eq (keymap-lookup global-map "C-c c o") 'work-kb-open-at-point))
        (should-not (keymap-lookup global-map "C-c k"))
        ;; 직접 붙이기
        (keymap-set global-map "<f8>" work-kb-command-map)
        (should (eq (keymap-lookup global-map "<f8> c") 'work-kb-capture))
        (should (eq (keymap-lookup global-map "<f8> l") 'work-kb-link)))
    (keymap-unset global-map "<f8>" t)
    (customize-set-variable 'work-kb-prefix-key "C-c k")
    (should (eq (keymap-lookup global-map "C-c k") work-kb-command-map))))

;;;; 캡처

(ert-deftest work-kb-test-capture-format ()
  (let ((work-kb--now-function (lambda () work-kb-test--time)))
    (should (equal (work-kb--format-capture "회의 [[KB-12]] @김철수") "- 09:12 회의 [[KB-12]] @김철수"))
    (should (equal (work-kb--format-capture "  앞뒤 공백  \n") "- 09:12 앞뒤 공백"))
    (should (equal (work-kb--format-capture "[ ] 경비 정산") "- [ ] 09:12 경비 정산"))
    (should (equal (work-kb--format-capture "첫 줄\n둘째 줄\r\n\n셋째") "- 09:12 첫 줄\n  둘째 줄\n  셋째"))
    (should (equal (work-kb--format-capture "[ ] 할 일\n세부") "- [ ] 09:12 할 일\n  세부"))
    (should-error (work-kb--format-capture "  \n ") :type 'user-error)))

(ert-deftest work-kb-test-capture-appends-to-record-end ()
  "`## 기록' 마지막 내용 줄 다음(빈 줄·다음 절 앞)에 넣고 나머지 바이트는 그대로."
  (work-kb-test--with-vault vault
    (let ((file (expand-file-name "daily/2026-11-05.md" vault)))
      (work-kb-test--write-bytes file work-kb-test--daily-lf)
      (cl-letf (((symbol-function 'work-kb--run-sync) (lambda (&rest _) (error "호출되면 안 됨"))))
        (work-kb-capture "회의 KB-12 한글 😀")
        (work-kb-capture "[ ] 회의실 예약"))
      (should (equal (work-kb-test--bytes file)
                     (work-kb-test--utf8
                      (concat "---\ndate: 2026-11-05\n---\n## 오늘\n<!-- kb:today:start -->\n<!-- kb:today:end -->\n\n"
                              "## 기록\n- 08:00 첫 기록\n- 09:12 회의 KB-12 한글 😀\n- [ ] 09:12 회의실 예약\n\n"
                              "## 메모\n자유 텍스트\n"))))
      (should-not (find-buffer-visiting file)))))

(ert-deftest work-kb-test-capture-crlf-file ()
  (work-kb-test--with-vault vault
    (let ((file (expand-file-name "daily/2026-11-05.md" vault)))
      (work-kb-test--write-bytes file (string-replace "\n" "\r\n" work-kb-test--daily-lf))
      (work-kb-capture "여러 줄\n둘째")
      (should (equal (work-kb-test--bytes file)
                     (work-kb-test--utf8
                      (string-replace
                       "\n" "\r\n"
                       (concat "---\ndate: 2026-11-05\n---\n## 오늘\n<!-- kb:today:start -->\n<!-- kb:today:end -->\n\n"
                               "## 기록\n- 08:00 첫 기록\n- 09:12 여러 줄\n  둘째\n\n## 메모\n자유 텍스트\n"))))))))

(ert-deftest work-kb-test-capture-no-record-heading ()
  "`## 기록'이 없으면 파일 끝에 만들고 기록한다 (D15). 앞 내용은 그대로."
  (work-kb-test--with-vault vault
    (let ((file (expand-file-name "daily/2026-11-05.md" vault)))
      (work-kb-test--write-bytes file "사람이 쓴 글")
      (work-kb-capture "메모")
      (should (equal (work-kb-test--bytes file)
                     (work-kb-test--utf8 "사람이 쓴 글\n\n## 기록\n- 09:12 메모\n"))))))

(ert-deftest work-kb-test-capture-live-buffer ()
  "파일이 열려 있으면 그 버퍼를 고치고 저장한다."
  (work-kb-test--with-vault vault
    (let* ((file (expand-file-name "daily/2026-11-05.md" vault))
           (_ (work-kb-test--write-bytes file work-kb-test--daily-lf))
           (buf (find-file-noselect file)))
      (work-kb-capture "열린 버퍼")
      (should (buffer-live-p buf))
      (with-current-buffer buf
        (should-not (buffer-modified-p))
        (should (string-match-p "- 08:00 첫 기록\n- 09:12 열린 버퍼\n" (buffer-string))))
      (should (string-match-p (regexp-quote (work-kb-test--utf8 "- 09:12 열린 버퍼\n\n## 메모"))
                              (work-kb-test--bytes file))))))

(ert-deftest work-kb-test-capture-missing-today ()
  "오늘 파일이 없으면 `kb.py today --date'를 부르고, 실패하면 뼈대를 직접 만든다."
  (work-kb-test--with-vault vault
    (let ((file (expand-file-name "daily/2026-11-05.md" vault))
          calls)
      (cl-letf (((symbol-function 'work-kb--run-sync)
                 (lambda (args &rest _) (push args calls) 1)))
        (work-kb-capture "첫 캡처"))
      (should (equal calls (list (list "today" "--root" (directory-file-name vault) "--date" "2026-11-05"))))
      (should (equal (work-kb-test--bytes file)
                     (work-kb-test--utf8
                      (concat "---\ndate: 2026-11-05\n---\n## 오늘\n<!-- kb:today:start -->\n<!-- kb:today:end -->\n\n"
                              "## 이월\n<!-- kb:carryover:start -->\n<!-- kb:carryover:end -->\n\n"
                              "## 기록\n- 09:12 첫 캡처\n")))))))

(ert-deftest work-kb-test-capture-buffer-finish ()
  (work-kb-test--with-vault vault
    (let ((file (expand-file-name "daily/2026-11-05.md" vault)))
      (work-kb-test--write-bytes file work-kb-test--daily-lf)
      (with-current-buffer (work-kb-capture)
        (should (derived-mode-p 'work-kb-capture-mode))
        (should (eq (keymap-lookup work-kb-capture-mode-map "C-c C-c") 'work-kb-capture-finish))
        (insert "버퍼에서 쓴 글")
        (work-kb-capture-finish))
      (should-not (get-buffer "*work-kb-capture*"))
      (should (string-match-p (regexp-quote (work-kb-test--utf8 "- 09:12 버퍼에서 쓴 글\n"))
                              (work-kb-test--bytes file))))))

;;;; 프로세스

(ert-deftest work-kb-test-run-ascii-argv ()
  "명령줄은 ASCII만, 한글 인수는 stdin JSON으로 (D10)."
  (work-kb-test--with-vault vault
    (let ((orig (symbol-function 'make-process))
          (python (work-kb-test--python))
          captured sent)
      (cl-letf (((symbol-function 'make-process)
                 (lambda (&rest plist)
                   (setq captured plist)
                   (apply orig (plist-put (copy-sequence plist) :command
                                          (append python (list "-c" "import sys; sys.stdin.buffer.read()"))))))
                ((symbol-function 'process-send-string)
                 (let ((send (symbol-function 'process-send-string)))
                   (lambda (proc string) (setq sent string) (funcall send proc string)))))
        (should (eq 0 (work-kb--run-sync (work-kb--kb-args "search" "한글 😀 질의")))))
      (let ((command (plist-get captured :command)))
        (should (equal (last command 4) '("-X" "utf8" "scripts/kb.py" "--argv-stdin")))
        (dolist (arg command) (should (work-kb-test--ascii-p arg))))
      (should (equal (plist-get captured :coding) '(utf-8-dos . utf-8-unix)))
      (should (eq (plist-get captured :connection-type) 'pipe))
      (should (equal (append (json-parse-string (if (multibyte-string-p sent) sent
                                                  (decode-coding-string sent 'utf-8)))
                             nil)
                     (list "search" "--root" (directory-file-name vault) "한글 😀 질의"))))))

(ert-deftest work-kb-test-json-stdin-roundtrip ()
  "\"한글 😀\"이 실제 Python을 거쳐 그대로 돌아오고, 버퍼에 상태 문구가 끼지 않는다."
  (let* ((orig (symbol-function 'make-process))
         (python (work-kb-test--python))
         (work-kb-root work-kb-test--repo)
         (code (concat "import sys, json; d = json.loads(sys.stdin.buffer.read().decode('utf-8')); "
                       "sys.stdout.buffer.write(json.dumps(d, ensure_ascii=False).encode('utf-8') + b'\\r\\n')"))
         (buf (get-buffer-create " *work-kb-test-echo*"))
         exit)
    (cl-letf (((symbol-function 'make-process)
               (lambda (&rest plist)
                 (apply orig (plist-put (copy-sequence plist) :command (append python (list "-c" code)))))))
      (setq exit (work-kb--wait (work-kb--run (list "echo" "한글 😀" "줄\n바꿈") :buffer buf :on-exit #'ignore))))
    (should (eq exit 0))
    (with-current-buffer buf
      (should-not (string-match-p "\r" (buffer-string)))
      (should-not (string-match-p "finished\\|Process" (buffer-string)))
      (should (equal (append (json-parse-string (string-trim (buffer-string))) nil)
                     (list "echo" "한글 😀" "줄\n바꿈"))))
    (kill-buffer buf)))

(ert-deftest work-kb-test-real-kb-py ()
  "실제 scripts/kb.py가 --argv-stdin으로 인수를 받고 UTF-8로 답한다."
  (let ((work-kb-root work-kb-test--repo)
        (work-kb-python nil))
    (should (eq 0 (work-kb--run-sync (list "--help"))))
    (with-current-buffer work-kb-output-buffer
      (should (string-match-p "사용법" (buffer-string))))))

(ert-deftest work-kb-test-hangul-root-relative-python ()
  "한글 루트에서도 .venv 상대 경로 인터프리터와 scripts/kb.py가 돈다 (D03)."
  (let ((venv-python (expand-file-name ".venv/Scripts/python.exe" work-kb-test--repo))
        (venv-cfg (expand-file-name ".venv/pyvenv.cfg" work-kb-test--repo)))
    (skip-unless (and (eq system-type 'windows-nt) (file-exists-p venv-python) (file-exists-p venv-cfg)))
    (let* ((root (work-kb-test--make-vault "한글 루트"))
           (work-kb-root root)
           (work-kb-vault nil)
           (work-kb-python nil)
           (out (get-buffer-create " *work-kb-test-root*")))
      (make-directory (expand-file-name ".venv/Scripts" root) t)
      (copy-file venv-python (expand-file-name ".venv/Scripts/python.exe" root) t)
      (copy-file venv-cfg (expand-file-name ".venv/pyvenv.cfg" root) t)
      (work-kb-test--write-bytes
       (expand-file-name "scripts/kb.py" root)
       (concat "import json, os, sys\n"
               "data = json.loads(sys.stdin.buffer.read().decode('utf-8'))\n"
               "out = {'argv': sys.argv[1:], 'stdin': data, 'cwd': os.getcwd(), 'utf8': sys.flags.utf8_mode}\n"
               "sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode('utf-8') + b'\\r\\n')\n"))
      (should (equal (work-kb--python-command) '(".venv/Scripts/python.exe")))
      (should (eq 0 (work-kb--wait (work-kb--run (work-kb--kb-args "status") :buffer out :on-exit #'ignore))))
      (let ((result (with-current-buffer out
                      (json-parse-string (string-trim (buffer-string)) :object-type 'alist))))
        (should (equal (append (alist-get 'argv result) nil) '("--argv-stdin")))
        (should (equal (append (alist-get 'stdin result) nil)
                       (list "status" "--root" (directory-file-name root))))
        (should (file-equal-p (alist-get 'cwd result) root))
        (should (eq (alist-get 'utf8 result) 1)))
      (kill-buffer out))))

(ert-deftest work-kb-test-python-resolution ()
  (let* ((base (work-kb-test--make-vault "python"))
         (store (expand-file-name "WindowsApps/" base))
         (bin (expand-file-name "bin/" base))
         (work-kb-root base)
         (work-kb-python nil))
    (make-directory store t)
    (make-directory bin t)
    (work-kb-test--write-bytes (expand-file-name "python.exe" store) "")
    (work-kb-test--write-bytes (expand-file-name "python.exe" bin) "")
    (cl-letf (((symbol-function 'call-process) (lambda (&rest _) (error "py -3를 시험하면 안 됨"))))
      ;; .venv 없음: PATH의 python, WindowsApps 별칭은 건너뜀
      (let ((exec-path (list store bin)))
        (should (equal (work-kb--python-command) (list (expand-file-name "python.exe" bin)))))
      ;; .venv 있음: 상대 경로
      (make-directory (expand-file-name ".venv/Scripts" base) t)
      (work-kb-test--write-bytes (expand-file-name ".venv/Scripts/python.exe" base) "")
      (let ((exec-path (list store bin)))
        (should (equal (work-kb--python-command) '(".venv/Scripts/python.exe"))))
      ;; 직접 지정이 우선
      (let ((work-kb-python '("py" "-3")))
        (should (equal (work-kb--python-command) '("py" "-3"))))
      (let ((work-kb-python "C:/Python/python.exe"))
        (should (equal (work-kb--python-command) '("C:/Python/python.exe")))))))

;;;; 이미지

(ert-deftest work-kb-test-clipboard-no-image ()
  "clip_image.ps1 종료 2 → 안내 메시지, 파일·daily 변화 없음."
  (skip-unless (eq system-type 'windows-nt))
  (work-kb-test--with-vault vault
    (let ((work-kb-root vault)
          (file (expand-file-name "daily/2026-11-05.md" vault))
          calls)
      (work-kb-test--write-bytes file work-kb-test--daily-lf)
      (work-kb-test--capturing-messages msgs
        (cl-letf (((symbol-function 'call-process)
                   (lambda (program &optional _infile _dest _display &rest args)
                     (push (cons program args) calls)
                     2))
                  ((symbol-function 'read-string) (lambda (&rest _) (error "물으면 안 됨"))))
          (should-not (work-kb-capture-clipboard-image)))
        (should (member "클립보드에 이미지가 없습니다" msgs)))
      (should (= 1 (length calls)))
      (let ((call (car calls)))
        (should (string-match-p "WindowsPowerShell/v1\\.0/powershell\\.exe\\'" (car call)))
        (should (equal (butlast (cdr call))
                       '("-NoProfile" "-NonInteractive" "-ExecutionPolicy" "Bypass" "-STA"
                         "-File" "scripts/clip_image.ps1" "-Out")))
        (should (equal (car (last call)) ".state/tmp/clip-20261105-091200.png"))
        (dolist (arg (cdr call)) (should (work-kb-test--ascii-p arg))))
      (should-not (directory-files (expand-file-name "raw/assets" vault) nil "\\`[^.]"))
      (should (equal (work-kb-test--bytes file) (work-kb-test--utf8 work-kb-test--daily-lf))))))

(ert-deftest work-kb-test-clipboard-saved ()
  "종료 0 → raw/assets/YYYY-MM-DD-HHMM-<이름>.png로 옮기고 `![[…]]' 줄을 캡처."
  (skip-unless (eq system-type 'windows-nt))
  (work-kb-test--with-vault vault
    (let ((work-kb-root vault)
          (file (expand-file-name "daily/2026-11-05.md" vault)))
      (work-kb-test--write-bytes file work-kb-test--daily-lf)
      (cl-letf (((symbol-function 'call-process)
                 (lambda (_program &optional _infile _dest _display &rest args)
                   (let ((out (expand-file-name (car (last args)) default-directory)))
                     (make-directory (file-name-directory out) t)
                     (let ((coding-system-for-write 'no-conversion))
                       (write-region "\x89PNG\r\n\x1a\nfake" nil out nil 'silent)))
                   0)))
        (let ((asset (work-kb-capture-clipboard-image "회의 화이트보드")))
          (should (equal (file-name-nondirectory asset) "2026-11-05-0912-회의 화이트보드.png"))
          (should (file-exists-p asset))))
      (should-not (directory-files (expand-file-name ".state/tmp" vault) nil "\\.png\\'"))
      (should (string-match-p
               (regexp-quote (work-kb-test--utf8 "- 09:12 ![[raw/assets/2026-11-05-0912-회의 화이트보드.png]] 회의 화이트보드\n"))
               (work-kb-test--bytes file))))))

(ert-deftest work-kb-test-image-file-never-overwrites ()
  (work-kb-test--with-vault vault
    (let ((src (expand-file-name "inbox/화면.PNG" vault))
          (file (expand-file-name "daily/2026-11-05.md" vault)))
      (work-kb-test--write-bytes file work-kb-test--daily-lf)
      (work-kb-test--write-bytes src "png")
      (let ((first (work-kb-capture-image-file src "설명"))
            (second (work-kb-capture-image-file src "")))
        (should (equal (file-name-nondirectory first) "2026-11-05-0912-화면.png"))
        (should (equal (file-name-nondirectory second) "2026-11-05-0912-화면-2.png")))
      (should (file-exists-p src))
      (let ((bytes (work-kb-test--bytes file)))
        (should (string-match-p (regexp-quote (work-kb-test--utf8 "- 09:12 ![[raw/assets/2026-11-05-0912-화면.png]] 설명\n")) bytes))
        (should (string-match-p (regexp-quote (work-kb-test--utf8 "- 09:12 ![[raw/assets/2026-11-05-0912-화면-2.png]]\n")) bytes)))
      (should-error (work-kb-capture-image-file (expand-file-name "daily/2026-11-05.md" vault)) :type 'user-error))))

;;;; 파일명·inbox

(ert-deftest work-kb-test-safe-name ()
  (should (equal (work-kb--safe-name "a<b>:c\"d/e\\f|g?h*i") "abcdefghi"))
  (should (equal (work-kb--safe-name "  여러   공백 . ") "여러 공백"))
  (should (equal (work-kb--safe-name "") "_"))
  (should (equal (work-kb--safe-name "...") "_"))
  (should (equal (work-kb--safe-name "CON") "_CON"))
  (should (equal (work-kb--safe-name "con.txt") "_con.txt"))
  (should (equal (work-kb--safe-name "COM¹") "_COM¹"))
  (should (equal (work-kb--safe-name "CONSOLE") "CONSOLE"))
  (should (= (length (work-kb--safe-name (make-string 100 ?가))) 80)))

(ert-deftest work-kb-test-inbox-filename ()
  (let ((work-kb--now-function (lambda () work-kb-test--time)))
    (should (equal (work-kb--inbox-file-name "회의: 결과?") "20261105-0912-회의 결과.md"))
    (should (equal (work-kb--inbox-title "" "\n# 첫 줄 제목\n본문") "첫 줄 제목"))
    (should (equal (work-kb--inbox-title nil "") "메모"))
    (should (equal (work-kb--inbox-title "  " (make-string 50 ?가)) (make-string 40 ?가)))
    (should (equal (work-kb--inbox-title "직접 제목" "본문") "직접 제목"))))

(ert-deftest work-kb-test-inbox-write ()
  (work-kb-test--with-vault vault
    (let ((first (work-kb-capture-inbox "" "아이디어: \"따옴표\" 메모\n둘째 줄"))
          (second (work-kb-capture-inbox "" "아이디어: \"따옴표\" 메모")))
      (should (equal (file-name-nondirectory first) "20261105-0912-아이디어 따옴표 메모.md"))
      (should (equal (file-name-nondirectory second) "20261105-0912-아이디어 따옴표 메모-2.md"))
      (let ((bytes (work-kb-test--bytes first)))
        (should (string-prefix-p (work-kb-test--utf8 "---\ncreated: 2026-11-05T09:12:00") bytes))
        (should (string-match-p (regexp-quote (work-kb-test--utf8 "title: \"아이디어: \\\"따옴표\\\" 메모\"\n---\n아이디어: \"따옴표\" 메모\n둘째 줄\n")) bytes))
        (should-not (string-match-p "\r" bytes))))))

;;;; 열기·검색

(ert-deftest work-kb-test-thing-at-point ()
  (with-temp-buffer
    (insert "회의 KB-12 와 [[OPS-3|배포]] 그리고 @김철수, [[설계 원칙#요약]] mail a@b.com xKB-1")
    (cl-flet ((at (needle) (goto-char (point-min)) (search-forward needle) (backward-char 1)
                  (work-kb--thing-at-point)))
      (should (equal (at "KB-12") '(key . "KB-12")))
      (should (equal (at "OPS-") '(key . "OPS-3")))
      (should (equal (at "김철") '(person . "김철수")))
      (should (equal (at "설계") '(link . "설계 원칙")))
      (should (equal (at "회의") nil))
      (should (equal (at "a@b") nil))
      (should (equal (at "xKB-1") nil)))))

(ert-deftest work-kb-test-open-key-with-cache ()
  (work-kb-test--with-vault vault
    (let ((page (expand-file-name "jira/KB-12.md" vault)))
      (work-kb-test--write-bytes page "---\nkey: KB-12\n---\n# KB-12\n")
      (cl-letf (((symbol-function 'work-kb--run-sync) (lambda (&rest _) (error "캐시가 있으면 호출 안 함"))))
        (with-temp-buffer
          (insert "회의 KB-12 논의")
          (goto-char (point-min)) (search-forward "KB-1")
          (work-kb-open-at-point)
          (should (file-equal-p (buffer-file-name) page)))))))

(ert-deftest work-kb-test-open-key-without-cache ()
  "캐시가 없으면 `kb.py link --stub KEY'를 부른 뒤 연다 (Phase 1)."
  (work-kb-test--with-vault vault
    (let ((page (expand-file-name "jira/KB-99.md" vault))
          calls)
      (cl-letf (((symbol-function 'work-kb--run-sync)
                 (lambda (args &rest _)
                   (push args calls)
                   (work-kb-test--write-bytes page "---\nkey: KB-99\nstub: true\n---\n# KB-99\n")
                   0)))
        (with-temp-buffer
          (insert "링크 [[KB-99]]")
          (goto-char (point-min)) (search-forward "KB-9")
          (work-kb-open-at-point)
          (should (file-equal-p (buffer-file-name) page))))
      (should (equal calls (list (list "link" "--root" (directory-file-name vault) "--stub" "KB-99")))))))

(ert-deftest work-kb-test-open-person-and-link ()
  (work-kb-test--with-vault vault
    (let ((person (expand-file-name "wiki/people/김철수.md" vault))
          (topic (expand-file-name "wiki/topics/설계 원칙.md" vault))
          (daily (expand-file-name "daily/2026-11-04.md" vault)))
      (work-kb-test--write-bytes person "# 김철수\n")
      (work-kb-test--write-bytes topic "# 설계 원칙\n")
      (work-kb-test--write-bytes daily "## 기록\n")
      (dolist (case (list (cons "@김철수" person) (cons "[[설계 원칙]]" topic) (cons "[[2026-11-04]]" daily)))
        (with-temp-buffer
          (insert (car case))
          (goto-char (+ (point-min) 2))
          (work-kb-open-at-point)
          (should (file-equal-p (buffer-file-name) (cdr case)))))
      (with-temp-buffer
        (insert "@이영희")
        (goto-char (point-min))
        (should-error (work-kb-open-at-point) :type 'user-error)))))

(ert-deftest work-kb-test-search-falls-back-to-kb-py ()
  (work-kb-test--with-vault vault
    (let (captured)
      (cl-letf (((symbol-function 'executable-find)
                 (lambda (name &rest _) (unless (equal name "rg") (error "다른 실행 파일 찾지 않음"))))
                ((symbol-function 'work-kb--run)
                 (lambda (args &rest _) (setq captured args) nil)))
        (should-not (fboundp 'consult-ripgrep))
        (work-kb-search "한글 질의"))
      (should (equal captured (list "search" "--root" (directory-file-name vault) "한글 질의"))))))

(ert-deftest work-kb-test-link-and-status-args ()
  (work-kb-test--with-vault vault
    (let (calls)
      (cl-letf (((symbol-function 'work-kb--run)
                 (lambda (args &rest _) (push args calls) nil)))
        (work-kb-link)
        (work-kb-status))
      (should (equal (reverse calls)
                     (list (list "link" "--root" (directory-file-name vault))
                           (list "status" "--root" (directory-file-name vault))))))))

;;; test-work-kb.el ends here
