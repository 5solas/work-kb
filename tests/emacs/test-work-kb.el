;;; test-work-kb.el --- work-kb.el ERT -*- lexical-binding: t; coding: utf-8 -*-

;; 실행: emacs -Q --batch --eval "(set-language-environment \"Korean\")" \
;;         -l tests/emacs/test-work-kb.el -f ert-run-tests-batch-and-exit
;; tests/test_emacs_ert.py가 콘솔 상속·CREATE_NO_WINDOW(cp949) 두 가지로 돌린다.
;; 환경변수: WORK_KB_TEST_TMP (ASCII 임시 폴더, 없으면 새로 만듦),
;;           WORK_KB_TEST_EXPECT_KOREAN=1 (한국어 언어 환경이 켜졌는지 확인),
;;           WORK_KB_TEST_EXPECT_LOCALE=cp949 등 (`locale-coding-system' 확인; 없으면 출력에 기록만).

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

(defun work-kb-test--write-raw (file bytes)
  "유니바이트 BYTES를 그대로 FILE에 쓴다."
  (make-directory (file-name-directory file) t)
  (let ((coding-system-for-write 'no-conversion))
    (write-region bytes nil file nil 'silent)))

(defun work-kb-test--rewrite-on-disk (file text)
  "다른 프로세스(link·daily_prepare)가 고친 것처럼 FILE을 TEXT로 다시 쓰고 수정 시각을 미룬다."
  (work-kb-test--write-bytes file text)
  (set-file-times file (time-add (current-time) 10)))

(defmacro work-kb-test--no-prompts (&rest body)
  "BODY 안에서 사용자에게 묻는 함수가 불리면 오류 (find-file의 \"changed on disk\" 등)."
  (declare (indent 0))
  `(cl-letf (((symbol-function 'yes-or-no-p) (lambda (&rest args) (error "물으면 안 됨: %S" args)))
             ((symbol-function 'y-or-n-p) (lambda (&rest args) (error "물으면 안 됨: %S" args)))
             ((symbol-function 'ask-user-about-supersession-threat)
              (lambda (&rest args) (error "물으면 안 됨: %S" args))))
     ,@body))

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

(ert-deftest work-kb-test-locale ()
  "러너가 기대한 콘솔 로캘인지 (D65). 기대값이 없으면 출력에 기록만 한다 (러너가 읽는다)."
  (message "WORK-KB-TEST-LOCALE: %s" locale-coding-system)
  (let ((expected (getenv "WORK_KB_TEST_EXPECT_LOCALE")))
    (when (and expected (not (string-empty-p expected)))
      (should (eq locale-coding-system (intern expected))))))

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

(defun work-kb-test--md-decoding (dir)
  "DIR 아래 daily/x.md를 읽을 때 고를 인코딩의 기본형 (등록 안 됐으면 nil 또는 다른 값)."
  (let ((found (find-operation-coding-system 'insert-file-contents
                                             (expand-file-name "daily/x.md" dir))))
    (and (consp found) (coding-system-base (car found)))))

(ert-deftest work-kb-test-vault-coding-setq-after-load ()
  "README 순서: (load …) 뒤에 (setq work-kb-vault …)를 해도 볼트 .md가 UTF-8로 등록된다."
  (let ((vault (work-kb-test--make-vault "나중 볼트"))
        (old-vault (default-value 'work-kb-vault)))
    (unwind-protect
        (progn
          (load work-kb-test--el nil t)
          (should-not (eq (work-kb-test--md-decoding vault) 'utf-8))
          (setq work-kb-vault vault)
          (should (eq (work-kb-test--md-decoding vault) 'utf-8))
          ;; 다른 볼트로 바꾸면 이전 볼트 패턴은 지워진다.
          (let ((other (work-kb-test--make-vault "다른 볼트")))
            (setq work-kb-vault other)
            (should (eq (work-kb-test--md-decoding other) 'utf-8))
            (should-not (eq (work-kb-test--md-decoding vault) 'utf-8))))
      (setq work-kb-vault old-vault)
      (work-kb--register-coding))))

(ert-deftest work-kb-test-vault-coding-lazy-in-accessors ()
  "감시자가 없어도(값이 등록과 달라도) 경로 함수가 부를 때 다시 등록한다."
  (let ((vault (work-kb-test--make-vault "지연 볼트"))
        (old-vault (default-value 'work-kb-vault)))
    (remove-variable-watcher 'work-kb-vault #'work-kb--coding-watcher)
    (unwind-protect
        (progn
          (setq work-kb-vault vault)
          (should-not (eq (work-kb-test--md-decoding vault) 'utf-8))
          (should (equal (work-kb--vault) vault))
          (should (eq (work-kb-test--md-decoding vault) 'utf-8)))
      (add-variable-watcher 'work-kb-vault #'work-kb--coding-watcher)
      (setq work-kb-vault old-vault)
      (work-kb--register-coding))))

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

(ert-deftest work-kb-test-capture-stale-live-buffer ()
  "열린 버퍼가 디스크보다 낡았으면: 수정이 없으면 다시 읽고 기록, 수정이 있으면 거부 (D26)."
  (work-kb-test--with-vault vault
    (let* ((file (expand-file-name "daily/2026-11-05.md" vault))
           (create-lockfiles nil)
           (disk (string-replace "- 08:00 첫 기록\n" "- 08:00 첫 기록 [[KB-12]]\n" work-kb-test--daily-lf)))
      (work-kb-test--write-bytes file work-kb-test--daily-lf)
      (let ((buf (let ((coding-system-for-read 'utf-8)) (find-file-noselect file))))
        ;; link가 KB-12 → [[KB-12]]로 정규화한 것처럼 디스크만 바뀜
        (work-kb-test--rewrite-on-disk file disk)
        (should-not (verify-visited-file-modtime buf))
        (work-kb-test--no-prompts (work-kb-capture "새 캡처"))
        (should (equal (work-kb-test--bytes file)
                       (work-kb-test--utf8
                        (string-replace "[[KB-12]]\n" "[[KB-12]]\n- 09:12 새 캡처\n" disk))))
        (with-current-buffer buf
          (should-not (buffer-modified-p))
          (should (string-match-p "\\[\\[KB-12\\]\\]\n- 09:12 새 캡처" (buffer-string))))
        ;; 버퍼도 수정됨 + 디스크 변경: 거부, 디스크 바이트 그대로
        (with-current-buffer buf (goto-char (point-max)) (insert "사람이 쓰는 중\n"))
        (let ((disk2 (concat disk "외부 변경\n")))
          (work-kb-test--rewrite-on-disk file disk2)
          (should-error (work-kb-test--no-prompts (work-kb-capture "거부될 캡처")) :type 'user-error)
          (should (equal (work-kb-test--bytes file) (work-kb-test--utf8 disk2)))
          (with-current-buffer buf
            (should (buffer-modified-p))
            (should (string-match-p "사람이 쓰는 중" (buffer-string)))
            (should-not (string-match-p "거부될 캡처" (buffer-string)))))))))

(ert-deftest work-kb-test-capture-refuses-non-utf-8 ()
  "cp949 daily에는 아무것도 붙이지 않고 파일 이름을 담은 사용자 오류를 낸다 (D11)."
  (work-kb-test--with-vault vault
    (let* ((file (expand-file-name "daily/2026-11-05.md" vault))
           (cp949 (encode-coding-string "## 기록\n- 09:00 한글 cp949\n" 'cp949)))
      (work-kb-test--write-raw file cp949)
      ;; 열린 버퍼 없음
      (let ((err (should-error (work-kb-test--no-prompts (work-kb-capture "새 줄")) :type 'user-error)))
        (should (string-match-p "UTF-8이 아닙니다" (cadr err)))
        (should (string-match-p (regexp-quote "2026-11-05.md") (cadr err))))
      (should (equal (work-kb-test--bytes file) cp949))
      (should-not (find-buffer-visiting file))
      ;; cp949로 열린 버퍼
      (let ((buf (let ((coding-system-for-read 'cp949)) (find-file-noselect file))))
        (let ((err (should-error (work-kb-test--no-prompts (work-kb-capture "라이브")) :type 'user-error)))
          (should (string-match-p "UTF-8이 아닙니다" (cadr err))))
        (should (equal (work-kb-test--bytes file) cp949))
        (with-current-buffer buf
          (should-not (buffer-modified-p))
          (should (eq (coding-system-base buffer-file-coding-system) (coding-system-base 'cp949)))
          (should (string-match-p "한글 cp949" (buffer-string)))
          (should-not (string-match-p "라이브" (buffer-string))))
        (kill-buffer buf))
      ;; UTF-8로 열려 날 바이트가 보이는 버퍼
      (let ((buf (let ((coding-system-for-read 'utf-8)) (find-file-noselect file))))
        (should-error (work-kb-test--no-prompts (work-kb-capture "라이브 2")) :type 'user-error)
        (should (equal (work-kb-test--bytes file) cp949))
        (with-current-buffer buf (should-not (buffer-modified-p)))))))

(ert-deftest work-kb-test-capture-bom-daily ()
  "BOM으로 시작하는 `## 기록' 첫 줄도 제목으로 본다: 두 번째 `## 기록'을 만들지 않는다."
  (work-kb-test--with-vault vault
    (let ((file (expand-file-name "daily/2026-11-05.md" vault)))
      (work-kb-test--write-bytes file "\ufeff## 기록\n- 09:00 첫 줄\n")
      (work-kb-capture "BOM 캡처")
      (should (equal (work-kb-test--bytes file)
                     (work-kb-test--utf8 "\ufeff## 기록\n- 09:00 첫 줄\n- 09:12 BOM 캡처\n"))))))

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
    ;; `py -3'은 늘 된다고 가정한다: 순서가 틀리면(py가 .venv나 PATH보다 먼저) 이 기계와 상관없이 드러난다.
    (let ((launcher t))
      (cl-letf (((symbol-function 'call-process) (lambda (&rest _) (error "py -3를 시험하면 안 됨")))
                ((symbol-function 'work-kb--py-launcher-works-p) (lambda () launcher)))
        ;; .venv 없음: PATH의 python, WindowsApps 별칭은 건너뜀
        (let ((exec-path (list store bin)))
          (should (equal (work-kb--python-command) (list (expand-file-name "python.exe" bin)))))
        ;; PATH에 별칭뿐이거나 아무것도 없음: 마지막 수단 py -3
        (let ((exec-path (list store)))
          (should (equal (work-kb--python-command) '("py" "-3"))))
        (let ((exec-path nil))
          (should (equal (work-kb--python-command) '("py" "-3"))))
        ;; py -3도 안 됨: 사용자 오류
        (setq launcher nil)
        (let ((exec-path nil))
          (should-error (work-kb--python-command) :type 'user-error))
        (setq launcher t)
        ;; .venv 있음: 상대 경로 (PATH python·py -3보다 먼저)
        (make-directory (expand-file-name ".venv/Scripts" base) t)
        (work-kb-test--write-bytes (expand-file-name ".venv/Scripts/python.exe" base) "")
        (let ((exec-path (list store bin)))
          (should (equal (work-kb--python-command) '(".venv/Scripts/python.exe"))))
        (let ((exec-path nil))
          (should (equal (work-kb--python-command) '(".venv/Scripts/python.exe"))))))
    (cl-letf (((symbol-function 'call-process) (lambda (&rest _) (error "py -3를 시험하면 안 됨"))))
      ;; 직접 지정이 우선
      (let ((work-kb-python '("py" "-3")))
        (should (equal (work-kb--python-command) '("py" "-3"))))
      (let ((work-kb-python "C:/Python/python.exe"))
        (should (equal (work-kb--python-command) '("C:/Python/python.exe")))))))

;;;; 오늘 (t)

(ert-deftest work-kb-test-today-forces-prepare ()
  "`t'는 파일이 있어도 `kb.py today --date'를 부르고(오늘·이월 구역 갱신) 그 파일을 연다."
  (work-kb-test--with-vault vault
    (let ((file (expand-file-name "daily/2026-11-05.md" vault))
          calls)
      (work-kb-test--write-bytes file work-kb-test--daily-lf)
      (cl-letf (((symbol-function 'work-kb--run-sync)
                 (lambda (args &rest _) (push args calls) 0)))
        (save-window-excursion
          (work-kb-test--no-prompts (work-kb-today))
          (should (buffer-file-name))
          (should (file-equal-p (buffer-file-name) file))))
      (should (equal calls (list (list "today" "--root" (directory-file-name vault) "--date" "2026-11-05")))))))

(ert-deftest work-kb-test-today-live-buffer ()
  "열린 오늘 버퍼: 수정 없으면 묻지 않고 새 내용, 수정 있으면 먼저 저장해 잃지 않는다,
버퍼와 파일이 모두 바뀌었으면 준비를 건너뛴다."
  (work-kb-test--with-vault vault
    (let ((file (expand-file-name "daily/2026-11-05.md" vault))
          (create-lockfiles nil)
          (make-backup-files nil)
          seen)
      (work-kb-test--write-bytes file work-kb-test--daily-lf)
      (cl-letf (((symbol-function 'work-kb--run-sync)
                 ;; daily_prepare 흉내: 디스크 내용을 읽어 오늘 구역에 한 줄을 넣고 다시 쓴다.
                 (lambda (_args &rest _)
                   (let ((text (decode-coding-string (work-kb-test--bytes file) 'utf-8)))
                     (push text seen)
                     (work-kb-test--rewrite-on-disk
                      file (string-replace "<!-- kb:today:start -->\n"
                                           "<!-- kb:today:start -->\n- [ ] KB-1 준비됨\n" text)))
                   0)))
        (let ((buf (let ((coding-system-for-read 'utf-8)) (find-file-noselect file))))
          ;; 1) 수정 없음: 묻지 않고 다시 읽는다
          (save-window-excursion
            (work-kb-test--no-prompts (work-kb-today))
            (should (eq (current-buffer) buf)))
          (should (= 1 (length seen)))
          (with-current-buffer buf
            (should-not (buffer-modified-p))
            (should (string-match-p "KB-1 준비됨" (buffer-string))))
          ;; 2) 저장 안 한 수정: 준비 전에 저장되어 daily_prepare가 보고, 버퍼에도 남는다
          (with-current-buffer buf (goto-char (point-max)) (insert "저장 안 한 글\n"))
          (save-window-excursion
            (work-kb-test--no-prompts (work-kb-today)))
          (should (= 2 (length seen)))
          (should (string-match-p "저장 안 한 글" (car seen)))
          (should (string-match-p (regexp-quote (work-kb-test--utf8 "저장 안 한 글\n"))
                                  (work-kb-test--bytes file)))
          (with-current-buffer buf
            (should-not (buffer-modified-p))
            (should (string-match-p "저장 안 한 글" (buffer-string)))
            (should (= 2 (how-many "KB-1 준비됨" (point-min) (point-max)))))
          ;; 3) 버퍼도 수정, 디스크도 바뀜: 준비를 건너뛰고 둘 다 그대로
          (with-current-buffer buf (goto-char (point-max)) (insert "둘 다 바뀜\n"))
          (let ((disk (concat (decode-coding-string (work-kb-test--bytes file) 'utf-8) "외부 변경\n")))
            (work-kb-test--rewrite-on-disk file disk)
            (work-kb-test--capturing-messages msgs
              (save-window-excursion
                (work-kb-test--no-prompts (work-kb-today))
                (should (eq (current-buffer) buf)))
              (should (cl-some (lambda (m) (string-match-p "건너뜁니다" m)) msgs)))
            (should (= 2 (length seen)))
            (should (equal (work-kb-test--bytes file) (work-kb-test--utf8 disk)))
            (with-current-buffer buf
              (should (buffer-modified-p))
              (should (string-match-p "둘 다 바뀜" (buffer-string))))))))))

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

(defun work-kb-test--fake-clip (code &optional output)
  "clip_image.ps1 대신: -Out 경로에 가짜 PNG를 만들고 OUTPUT을 출력한 뒤 CODE로 끝난다."
  (lambda (_program &optional _infile _dest _display &rest args)
    (let ((out (expand-file-name (car (last args)) default-directory)))
      (make-directory (file-name-directory out) t)
      (let ((coding-system-for-write 'no-conversion))
        (write-region "\x89PNG\r\n\x1a\nfake" nil out nil 'silent)))
    (when output (insert output))
    code))

(ert-deftest work-kb-test-clipboard-empty-name ()
  "이름을 비우면 capture (D16)."
  (skip-unless (eq system-type 'windows-nt))
  (work-kb-test--with-vault vault
    (let ((work-kb-root vault)
          (file (expand-file-name "daily/2026-11-05.md" vault)))
      (work-kb-test--write-bytes file work-kb-test--daily-lf)
      (cl-letf (((symbol-function 'call-process) (work-kb-test--fake-clip 0)))
        (let ((asset (work-kb-capture-clipboard-image "")))
          (should (equal (file-name-nondirectory asset) "2026-11-05-0912-capture.png"))
          (should (file-exists-p asset))))
      (should (string-match-p
               (regexp-quote (work-kb-test--utf8 "- 09:12 ![[raw/assets/2026-11-05-0912-capture.png]]\n"))
               (work-kb-test--bytes file))))))

(ert-deftest work-kb-test-clipboard-error-cleans-temp ()
  "종료 3 → 임시 PNG를 지우고 F(파일) 캡처를 안내, daily·assets 변화 없음."
  (skip-unless (eq system-type 'windows-nt))
  (work-kb-test--with-vault vault
    (let ((work-kb-root vault)
          (file (expand-file-name "daily/2026-11-05.md" vault)))
      (work-kb-test--write-bytes file work-kb-test--daily-lf)
      (work-kb-test--capturing-messages msgs
        (cl-letf (((symbol-function 'call-process) (work-kb-test--fake-clip 3 "clip_image: boom"))
                  ((symbol-function 'read-string) (lambda (&rest _) (error "물으면 안 됨"))))
          (should-not (work-kb-capture-clipboard-image)))
        (should (cl-some (lambda (m) (and (string-match-p "F(파일)" m) (string-match-p "종료 3" m)
                                          (string-match-p "clip_image: boom" m)))
                         msgs)))
      (should-not (directory-files (expand-file-name ".state/tmp" vault) nil "\\`[^.]"))
      (should-not (directory-files (expand-file-name "raw/assets" vault) nil "\\`[^.]"))
      (should (equal (work-kb-test--bytes file) (work-kb-test--utf8 work-kb-test--daily-lf))))))

(ert-deftest work-kb-test-link-safe-asset-and-inbox ()
  "링크 대상이 되는 이름(이미지·inbox)은 `[ ] # ^ |'를 `-'로 바꿔 `![[…]]'·`[[…]]'가 깨지지 않는다."
  (should (equal (work-kb--link-safe-name "회의 [초안] #2") "회의 -초안- -2"))
  (should (equal (work-kb--link-safe-name "a^b|c]") "a-bc-"))
  (should (equal (work-kb--link-safe-name "CON") "_CON"))
  (should (equal (work-kb--link-safe-name " . ") "_"))
  ;; core.safe_name과 같은 규칙은 그대로 (사람 페이지 등)
  (should (equal (work-kb--safe-name "이슈 #12 [a]") "이슈 #12 [a]"))
  (work-kb-test--with-vault vault
    (let ((file (expand-file-name "daily/2026-11-05.md" vault))
          (src (expand-file-name "inbox/회의 [초안] #2.png" vault)))
      (work-kb-test--write-bytes file work-kb-test--daily-lf)
      (work-kb-test--write-bytes src "png")
      (let* ((asset (work-kb-capture-image-file src "설명"))
             (daily (decode-coding-string (work-kb-test--bytes file) 'utf-8)))
        (should (equal (file-name-nondirectory asset) "2026-11-05-0912-회의 -초안- -2.png"))
        ;; notes.image_embeds와 같은 꼴: ![[ 다음 ] 또는 | 앞까지가 대상
        (should (string-match "!\\[\\[\\([^]|]+\\)\\]\\]" daily))
        (let ((target (match-string 1 daily)))
          (should (equal target "raw/assets/2026-11-05-0912-회의 -초안- -2.png"))
          (should-not (string-match-p "#" target))
          (should (file-equal-p (expand-file-name target vault) asset))))
      (let* ((note (work-kb-capture-inbox "회의 [초안] #2" "본문"))
             (stem (file-name-base note))
             (link (concat "[[inbox/" stem "]]")))
        (should (equal (file-name-nondirectory note) "20261105-0912-회의 -초안- -2.md"))
        (should-not (string-match-p "[][#^|]" stem))
        ;; notes.wikilinks와 같은 꼴: `#'·`|'·`]' 앞에서 끊긴다
        (should (string-match "\\[\\[\\([^]|#]+\\)" link))
        (should (equal (match-string 1 link) (concat "inbox/" stem)))))))

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

(defconst work-kb-test--jira-full
  "---\nkey: %s\nproject: KB\nsummary: \"캐시 동기화\"\nstatus: 진행 중\nstatus_category: indeterminate\n---\n# %s 캐시 동기화\n\n## 노트에서 언급\n<!-- kb:mentions:start -->\n<!-- kb:mentions:end -->\n"
  "jira_sync가 쓴 것 같은 페이지 (%s 둘 = 키).")

(defconst work-kb-test--jira-stub
  "---\nkey: %s\nproject: KB\nstub: true\nsummary: \"\"\nstatus: \"\"\n---\n# %s\n\n> 아직 동기화되지 않은 일감입니다.\n\n## 노트에서 언급\n<!-- kb:mentions:start -->\n<!-- kb:mentions:end -->\n"
  "notes.page_template(\"jira\")와 같은 꼴의 stub (%s 둘 = 키).")

(defun work-kb-test--jira-page (template key) (format template key key))

(defmacro work-kb-test--fake-run (calls handler &rest body)
  "BODY 동안 `work-kb--run'을 가짜로: ARGS·:buffer를 CALLS에 (args . buffer)로 쌓고
\(HANDLER ARGS)를 종료 코드로 삼아 :on-exit를 곧바로 부른다 (센티널 흉내). `kb.py'는 돌지 않는다."
  (declare (indent 2))
  `(let ((work-kb--syncing-keys nil))
     (cl-letf (((symbol-function 'work-kb--run)
                (lambda (args &rest plist)
                  (push (cons args (plist-get plist :buffer)) ,calls)
                  (let ((code (funcall ,handler args))
                        (on-exit (plist-get plist :on-exit)))
                    (when on-exit (funcall on-exit nil code)))
                  'fake-proc))
               ((symbol-function 'work-kb--run-sync) (lambda (&rest _) (error "동기 실행은 쓰지 않음"))))
       ,@body)))

(defun work-kb-test--open-key-at (text needle)
  "TEXT를 담은 임시 버퍼에서 NEEDLE 위에 커서를 두고 `o'. (돌려준 값 . 창의 버퍼)."
  (with-temp-buffer
    (insert text)
    (goto-char (point-min)) (search-forward needle)
    (let ((result (work-kb-open-at-point)))
      (cons result (window-buffer (selected-window))))))

(ert-deftest work-kb-test-jira-stub-p ()
  "front matter의 `stub: true'만 stub으로 본다 (BOM·CRLF·대소문자 허용, 본문 무시)."
  (work-kb-test--with-vault vault
    (let ((file (expand-file-name "jira/KB-1.md" vault)))
      (dolist (case `((,(work-kb-test--jira-page work-kb-test--jira-stub "KB-1") . t)
                      (,(string-replace "\n" "\r\n" (work-kb-test--jira-page work-kb-test--jira-stub "KB-1")) . t)
                      (,(concat "﻿" (work-kb-test--jira-page work-kb-test--jira-stub "KB-1")) . t)
                      ("---\nkey: KB-1\nstub: True  # link\n---\n" . t)
                      ("---\nstub: 'true'\n---\n" . t)
                      (,(work-kb-test--jira-page work-kb-test--jira-full "KB-1") . nil)
                      ("---\nkey: KB-1\nstub: false\n---\n" . nil)
                      ("---\nkey: KB-1\nstubborn: true\n---\n" . nil)
                      ("---\nkey: KB-1\n---\nstub: true\n" . nil)
                      ("# 제목만\nstub: true\n" . nil)
                      ("" . nil)))
        (work-kb-test--write-bytes file (car case))
        (should (eq (cdr case) (and (work-kb--jira-stub-p file) t)))))))

(ert-deftest work-kb-test-open-key-with-cache ()
  "동기화된 페이지가 있으면 sync 없이 바로 연다."
  (work-kb-test--with-vault vault
    (let ((page (expand-file-name "jira/KB-12.md" vault))
          calls)
      (work-kb-test--write-bytes page (work-kb-test--jira-page work-kb-test--jira-full "KB-12"))
      (work-kb-test--fake-run calls (lambda (_) (error "캐시가 있으면 호출 안 함"))
        (save-window-excursion
          (let ((buf (cdr (work-kb-test--open-key-at "회의 KB-12 논의" "KB-1"))))
            (should (file-equal-p (buffer-file-name buf) page)))))
      (should-not calls))))

(ert-deftest work-kb-test-open-key-missing-syncs-then-opens ()
  "캐시가 없으면 `kb.py sync --root V --key KEY'를 자기 버퍼로 돌리고, 성공하면 연다."
  (work-kb-test--with-vault vault
    (let ((page (expand-file-name "jira/KB-99.md" vault))
          calls)
      (work-kb-test--capturing-messages msgs
        (work-kb-test--fake-run calls
            (lambda (_args)
              (work-kb-test--write-bytes page (work-kb-test--jira-page work-kb-test--jira-full "KB-99"))
              0)
          (save-window-excursion
            (let ((buf (cdr (work-kb-test--open-key-at "링크 [[KB-99]]" "KB-9"))))
              (should (file-equal-p (buffer-file-name buf) page))
              (should (string-match-p "캐시 동기화" (with-current-buffer buf (buffer-string)))))))
        (should (cl-some (lambda (m) (string-match-p "KB-99 동기화했습니다" m)) msgs)))
      (should (equal calls (list (cons (list "sync" "--root" (directory-file-name vault) "--key" "KB-99")
                                       "*work-kb-sync KB-99*"))))
      (should-not work-kb--syncing-keys))))

(ert-deftest work-kb-test-open-key-stub-syncs-and-reverts ()
  "stub이 열려 있어도: sync 성공 뒤 그 버퍼를 묻지 않고 다시 읽어 보인다."
  (work-kb-test--with-vault vault
    (let ((page (expand-file-name "jira/KB-12.md" vault))
          (create-lockfiles nil)
          calls)
      (work-kb-test--write-bytes page (work-kb-test--jira-page work-kb-test--jira-stub "KB-12"))
      (let ((stub-buf (let ((coding-system-for-read 'utf-8)) (find-file-noselect page))))
        (work-kb-test--fake-run calls
            (lambda (_args)
              (work-kb-test--rewrite-on-disk page (work-kb-test--jira-page work-kb-test--jira-full "KB-12"))
              0)
          (save-window-excursion
            (work-kb-test--no-prompts
              (should (eq (cdr (work-kb-test--open-key-at "회의 KB-12" "KB-1")) stub-buf)))))
        (with-current-buffer stub-buf
          (should-not (buffer-modified-p))
          (should (string-match-p "status_category: indeterminate" (buffer-string)))
          (should-not (string-match-p "stub: true" (buffer-string)))))
      (should (equal (mapcar #'car calls)
                     (list (list "sync" "--root" (directory-file-name vault) "--key" "KB-12")))))))

(ert-deftest work-kb-test-open-key-sync-fails-makes-stub ()
  "sync 실패(네트워크 3)·캐시 없음: `kb.py link --stub KEY'로 만들어 열고 이유를 알린다."
  (work-kb-test--with-vault vault
    (let ((page (expand-file-name "jira/KB-99.md" vault))
          calls)
      (work-kb-test--capturing-messages msgs
        (work-kb-test--fake-run calls
            (lambda (args)
              (pcase (car args)
                ("sync" (with-current-buffer (get-buffer-create "*work-kb-sync KB-99*")
                          (erase-buffer) (insert "jira_sync: 연결 실패 (주소.invalid)\n"))
                        3)
                ("link" (work-kb-test--write-bytes page (work-kb-test--jira-page work-kb-test--jira-stub "KB-99"))
                        0)))
          (save-window-excursion
            (let ((buf (cdr (work-kb-test--open-key-at "KB-99 확인" "KB-9"))))
              (should (file-equal-p (buffer-file-name buf) page)))))
        (should (cl-some (lambda (m) (and (string-match-p "KB-99 동기화 실패" m)
                                          (string-match-p "종료 3: 네트워크" m)
                                          (string-match-p "연결 실패" m)
                                          (string-match-p "stub" m)))
                         msgs)))
      (should (equal (mapcar #'car (reverse calls))
                     (list (list "sync" "--root" (directory-file-name vault) "--key" "KB-99")
                           (list "link" "--root" (directory-file-name vault) "--stub" "KB-99"))))
      (should (get-buffer "*work-kb-sync KB-99*"))
      (kill-buffer "*work-kb-sync KB-99*")
      (should-not work-kb--syncing-keys))))

(ert-deftest work-kb-test-open-key-sync-fails-keeps-stub ()
  "stub이 이미 있고 sync가 실패(설정 4)하면 link 없이 stub을 열고 알린다."
  (work-kb-test--with-vault vault
    (let ((page (expand-file-name "jira/OPS-3.md" vault))
          calls)
      (work-kb-test--write-bytes page (work-kb-test--jira-page work-kb-test--jira-stub "OPS-3"))
      (work-kb-test--capturing-messages msgs
        (work-kb-test--fake-run calls (lambda (_) 4)
          (save-window-excursion
            (let ((buf (cdr (work-kb-test--open-key-at "배포 [[OPS-3]]" "OPS"))))
              (should (file-equal-p (buffer-file-name buf) page)))))
        (should (cl-some (lambda (m) (and (string-match-p "OPS-3 동기화 실패" m)
                                          (string-match-p "종료 4: 설정" m)))
                         msgs)))
      (should (equal (mapcar #'caar calls) '("sync")))
      (should (equal (work-kb-test--bytes page)
                     (work-kb-test--utf8 (work-kb-test--jira-page work-kb-test--jira-stub "OPS-3")))))))

(ert-deftest work-kb-test-open-key-sync-ok-but-missing ()
  "sync가 0으로 끝났는데도 페이지가 없으면(Jira에 없는 키) stub으로 물러선다."
  (work-kb-test--with-vault vault
    (let ((page (expand-file-name "jira/KB-404.md" vault))
          calls)
      (work-kb-test--capturing-messages msgs
        (work-kb-test--fake-run calls
            (lambda (args)
              (when (equal (car args) "link")
                (work-kb-test--write-bytes page (work-kb-test--jira-page work-kb-test--jira-stub "KB-404")))
              0)
          (save-window-excursion
            (should (file-equal-p (buffer-file-name (cdr (work-kb-test--open-key-at "KB-404" "KB-4"))) page))))
        (should (cl-some (lambda (m) (string-match-p "Jira에서 받아 오지 못함" m)) msgs)))
      (should (equal (mapcar #'caar (reverse calls)) '("sync" "link"))))))

(ert-deftest work-kb-test-open-key-sync-in-flight ()
  "같은 키의 sync가 도는 중에 다시 `o'를 누르면 또 돌리지 않는다."
  (work-kb-test--with-vault vault
    (let ((calls nil)
          (work-kb--syncing-keys nil))
      (cl-letf (((symbol-function 'work-kb--run)
                 (lambda (args &rest _) (push args calls) 'fake-proc)))
        (work-kb-test--capturing-messages msgs
          (should (eq (car (work-kb-test--open-key-at "KB-7" "KB-")) 'fake-proc))
          (should-not (car (work-kb-test--open-key-at "KB-7" "KB-")))
          (should (cl-some (lambda (m) (string-match-p "이미 진행 중" m)) msgs))))
      (should (= 1 (length calls)))
      (should (equal work-kb--syncing-keys '("KB-7"))))))

(ert-deftest work-kb-test-open-key-real-async-sentinel ()
  "실제 비동기 프로세스: 센티널이 끝난 뒤 페이지를 연다 (sync 대신 stdin JSON을 읽어 쓰는 Python)."
  (work-kb-test--with-vault vault
    (let* ((orig (symbol-function 'make-process))
           (python (work-kb-test--python))
           (page (expand-file-name "jira/KB-12.md" vault))
           (code (concat "import sys, json, pathlib; a = json.loads(sys.stdin.buffer.read().decode('utf-8')); "
                         "assert a[0] == 'sync'; root = a[a.index('--root') + 1]; key = a[a.index('--key') + 1]; "
                         "pathlib.Path(root, 'jira', key + '.md').write_bytes(('---\\nkey: ' + key + "
                         "'\\nsummary: \"\\ube44\\ub3d9\\uae30 \\ub3d9\\uae30\\ud654\"\\nstatus_category: new\\n---\\n# ' + key + '\\n').encode('utf-8'))"))
           (work-kb--syncing-keys nil)
           captured)
      (cl-letf (((symbol-function 'make-process)
                 (lambda (&rest plist)
                   (setq captured plist)
                   (apply orig (plist-put (copy-sequence plist) :command (append python (list "-c" code)))))))
        (save-window-excursion
          (let* ((proc (car (work-kb-test--open-key-at "회의 KB-12" "KB-1"))))
            (should (processp proc))
            (should-not (file-exists-p page))
            (should (eq 0 (work-kb--wait proc 60)))
            (let ((buf (window-buffer (selected-window))))
              (should (file-equal-p (buffer-file-name buf) page))
              (should (string-match-p "비동기 동기화" (with-current-buffer buf (buffer-string))))))))
      (should (equal (last (plist-get captured :command) 4) '("-X" "utf8" "scripts/kb.py" "--argv-stdin")))
      (should-not (get-buffer "*work-kb-sync KB-12*"))
      (should-not work-kb--syncing-keys))))

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

;;;; Jira 작업 (Phase 3, `j' transient)

(defconst work-kb-test--jira-keys
  '(("v" . work-kb-jira-view) ("c" . work-kb-jira-comment) ("t" . work-kb-jira-transition)
    ("a" . work-kb-jira-assign) ("w" . work-kb-jira-worklog) ("n" . work-kb-jira-create)
    ("p" . work-kb-jira-promote) ("q" . work-kb-jira-jql) ("S" . work-kb-jira-sync)
    ("f" . work-kb-jira-flush) ("d" . work-kb-jira-check-done))
  "§6.8 transient 키 (D53의 `a' 포함).")

(defvar work-kb-test--prompts nil "가짜 입력 함수가 받은 프롬프트 (최근 것이 앞).")

(defmacro work-kb-test--answering (reads chooses yes &rest body)
  "BODY 동안 입력을 가짜로: `read-string'은 READS를 차례로(:initial이면 INITIAL-INPUT 그대로),
`completing-read'는 CHOOSES를 차례로, `y-or-n-p'는 YES를 돌려준다 (:none이면 물으면 오류).
더 물으면 오류. 프롬프트는 `work-kb-test--prompts'에 쌓는다 (completing-read는 (프롬프트 . 후보))."
  (declare (indent 3))
  `(let ((reads ,reads) (chooses ,chooses) (yes ,yes))
     (setq work-kb-test--prompts nil)
     (cl-letf (((symbol-function 'read-string)
                (lambda (prompt &optional initial &rest _)
                  (push prompt work-kb-test--prompts)
                  (unless reads (error "read-string을 더 묻지 않아야 함: %s" prompt))
                  (let ((answer (pop reads))) (if (eq answer :initial) (or initial "") answer))))
               ((symbol-function 'completing-read)
                (lambda (prompt collection &rest _)
                  (push (cons prompt (all-completions "" collection)) work-kb-test--prompts)
                  (unless chooses (error "completing-read를 더 묻지 않아야 함: %s" prompt))
                  (pop chooses)))
               ((symbol-function 'y-or-n-p)
                (lambda (prompt)
                  (push prompt work-kb-test--prompts)
                  (when (eq yes :none) (error "확인을 묻지 않아야 함: %s" prompt))
                  yes)))
       ,@body)))

(defun work-kb-test--emit (buffer text code)
  "가짜 실행: BUFFER를 TEXT로 채우고 CODE를 돌려준다."
  (with-current-buffer (get-buffer-create buffer)
    (let ((inhibit-read-only t)) (erase-buffer) (insert text)))
  code)

(defconst work-kb-test--jira-daily
  (concat "---\ndate: 2026-11-05\n---\n## 오늘\n<!-- kb:today:start -->\n"
          "- [ ] [[KB-12]] 캐시 동기화\n<!-- kb:today:end -->\n\n## 기록\n"
          "- 08:00 첫 기록\n"                          ; 10
          "- 09:12 회의 [[KB-12]] 배포 일정 확정\n"     ; 11
          "- [ ] 09:30 경비 정산\n")                    ; 12
  "줄 번호: 10 첫 기록, 11 KB-12 회의, 12 키 없는 할 일.")

(defun work-kb-test--visit-daily (vault needle &optional text)
  "VAULT의 daily/2026-11-05.md를 (TEXT 또는 기본 내용으로) 쓰고 열어 NEEDLE 위에 커서를 둔다."
  (let ((file (expand-file-name "daily/2026-11-05.md" vault)))
    (work-kb-test--write-bytes file (or text work-kb-test--jira-daily))
    (let ((buf (let ((coding-system-for-read 'utf-8)) (find-file-noselect file))))
      (set-buffer buf)
      (goto-char (point-min))
      (search-forward needle)
      (backward-char 1)
      buf)))

(defun work-kb-test--act (vault sub &rest args)
  (append (list "act" sub "--root" (directory-file-name vault)) args))

(ert-deftest work-kb-test-jira-transient-keys ()
  "j는 transient `work-kb-jira'이고 §6.8의 모든 키가 제 명령에 묶여 있다."
  (should (eq (keymap-lookup work-kb-command-map "j") 'work-kb-jira))
  (should (commandp 'work-kb-jira))
  (dolist (entry work-kb-test--jira-keys)
    (let ((suffix (transient-get-suffix 'work-kb-jira (car entry))))
      (should suffix)
      (should (string-match-p (format ":command %s\\_>" (cdr entry)) (format "%S" suffix)))
      (should (commandp (cdr entry))))))

(ert-deftest work-kb-test-jira-key-at-point ()
  "커서 위치 → 줄의 첫 키 → jira/KEY.md 파일 이름. 없으면 nil."
  (with-temp-buffer
    (insert "회의 [[KB-12]] 와 OPS-3 배포\n키 없는 줄 xKB-1\n")
    (goto-char (point-min)) (search-forward "OPS-")
    (should (equal (work-kb--key-at-point) "OPS-3"))
    (goto-char (point-min)) (search-forward "배포")
    (should (equal (work-kb--key-at-point) "KB-12"))
    (forward-line 1)
    (should-not (work-kb--key-at-point)))
  (with-temp-buffer
    (cl-flet ((at (line needle)
                (erase-buffer) (insert line)
                (goto-char (point-min)) (search-forward needle) (backward-char 1)
                (work-kb--key-at-point)))
      ;; 링크 대상은 `jira/'·`.md'·별칭·제목을 떼고 본다 (줄의 첫 키 OPS-3으로 새지 않는다).
      (should (equal (at "- [[OPS-3]] 와 [[jira/KB-12]] 비교" "jira/KB") "KB-12"))
      (should (equal (at "- [[OPS-3]] 와 [[jira/KB-12|별칭]] 비교" "별칭") "KB-12"))
      (should (equal (at "- [[OPS-3]] 와 [[KB-12#h]] 비교" "#h") "KB-12"))
      (should (equal (at "- [[OPS-3]] 와 [[jira/KB-12.md]] 비교" ".md") "KB-12"))
      ;; 키가 아닌 링크 위면 nil (사용자에게 묻는다).
      (should-not (at "- [[KB-12-설계]] 회의 OPS-3" "설계"))
      (should-not (at "- OPS-3 와 [[projects/KB]]" "projects"))
      ;; 링크 밖이면 줄의 첫 키. `KB-12-설계'(문서 이름)는 키가 아니다.
      (should (equal (at "- [[KB-12-설계]] 회의 OPS-3" "회의") "OPS-3"))
      (should-not (at "- KB-12-설계 회의" "회의"))
      (should-not (at "- KB-12-설계 회의" "KB-1")))
    ;; 키가 아닌 링크 위에서 c/a/w/t는 줄의 다른 키 대신 키를 묻는다.
    (erase-buffer) (insert "- OPS-3 와 [[KB-12-설계]]")
    (goto-char (point-min)) (search-forward "설계")
    (work-kb-test--answering '(" kb-7 ") nil :none
      (should (equal (work-kb--jira-key) "KB-7")))
    (should (equal work-kb-test--prompts '("Jira 키: "))))
  (work-kb-test--with-vault vault
    (let ((page (expand-file-name "jira/OPS-7.md" vault)))
      (work-kb-test--write-bytes page "---\nkey: OPS-7\n---\n# 제목\n")
      (with-current-buffer (let ((coding-system-for-read 'utf-8)) (find-file-noselect page))
        (goto-char (point-max))
        (should (equal (work-kb--key-at-point) "OPS-7"))))))

(ert-deftest work-kb-test-jira-view ()
  "v: 키가 있으면 묻지 않고 `o'와 같은 길로 연다. 없으면 묻고 대문자로 바꾼다."
  (work-kb-test--with-vault vault
    (let (opened)
      (cl-letf (((symbol-function 'work-kb--open-key) (lambda (key) (push key opened))))
        (with-temp-buffer
          (insert "회의 KB-12")
          (work-kb-test--answering nil nil :none (work-kb-jira-view))
          (erase-buffer) (insert "키 없음")
          (work-kb-test--answering '(" kb-7 ") nil :none (work-kb-jira-view))
          (work-kb-test--answering '("아님") nil :none
            (should-error (work-kb-jira-view) :type 'user-error))))
      (should (equal opened '("KB-7" "KB-12"))))))

(ert-deftest work-kb-test-jira-comment-from-daily-line ()
  "c (daily, 본문 그대로): `--from-line daily/…:N'. 저장 안 된 수정은 먼저 저장한다."
  (work-kb-test--with-vault vault
    (let (calls)
      (work-kb-test--fake-run calls (lambda (_) 0)
        (save-current-buffer
          (let ((buf (work-kb-test--visit-daily vault "배포")))
            ;; 위 줄을 고쳐 두면(저장 안 함) 디스크와 줄 번호를 맞추려고 저장한다.
            (save-excursion (goto-char (point-min)) (search-forward "첫 기록") (insert " 추가"))
            (should (buffer-modified-p buf))
            (work-kb-test--answering '(:initial) nil :none (work-kb-jira-comment))
            (should-not (buffer-modified-p buf))
            (should (string-match-p "첫 기록 추가"
                                    (decode-coding-string (work-kb-test--bytes (buffer-file-name buf)) 'utf-8)))
            (should (member "KB-12 댓글: " work-kb-test--prompts)))))
      (should (equal calls (list (cons (work-kb-test--act vault "comment" "KB-12"
                                                          "--from-line" "daily/2026-11-05.md:11")
                                       "*work-kb-jira*")))))))

(ert-deftest work-kb-test-jira-comment-body ()
  "c: 본문을 고쳤거나 daily가 아니면 `--body'. 빈 본문은 보내지 않는다."
  (work-kb-test--with-vault vault
    (let (calls)
      (work-kb-test--fake-run calls (lambda (_) 0)
        (save-current-buffer
          (work-kb-test--visit-daily vault "배포")
          (work-kb-test--answering '("고친 댓글 😀") nil :none (work-kb-jira-comment))
          (work-kb-test--answering '("  ") nil :none
            (should-error (work-kb-jira-comment) :type 'user-error)))
        (with-temp-buffer
          (insert "OPS-3 배포 완료")
          (work-kb-test--answering '(:initial) nil :none (work-kb-jira-comment))))
      (should (equal (mapcar #'car (reverse calls))
                     (list (work-kb-test--act vault "comment" "KB-12" "--body" "고친 댓글 😀")
                           (work-kb-test--act vault "comment" "OPS-3" "--body" "OPS-3 배포 완료")))))))

(ert-deftest work-kb-test-jira-transition-choices ()
  "전환 목록 JSON → ((`이름 → 도착 (id)' . id) …): 객체 배열·{transitions}·문자열 배열, 앞의 로그 줄,
깨진 출력. 이름이 같은 전환도 한 줄씩 남는다."
  (let ((parse (lambda (text) (work-kb--transition-choices (work-kb--parse-json-output text)))))
    (should (equal (funcall parse "[{\"id\":\"21\",\"name\":\"In Review\"},{\"id\":\"31\",\"name\":\"완료\"}]")
                   '(("In Review (21)" . "21") ("완료 (31)" . "31"))))
    (should (equal (funcall parse (concat "jira_act: 경고 [무시]\n{\"key\":\"KB-1\",\"transitions\":["
                                          "{\"id\":\"41\",\"name\":\"Done\",\"to\":{\"name\":\"Done\"}},"
                                          "{\"id\":42,\"name\":\"Done\",\"to\":\"Closed\"}]}\n"))
                   '(("Done → Done (41)" . "41") ("Done → Closed (42)" . "42"))))
    (should (equal (funcall parse "[\"Start\", \"Stop\"]") '(("Start" . "Start") ("Stop" . "Stop"))))
    (should (equal (funcall parse "[{\"name\":\"Go\",\"to\":\"\"}]") '(("Go" . "Go"))))
    (should (equal (funcall parse "[]") nil))
    (should (equal (funcall parse "오류: 연결 실패\n") nil))
    (should (equal (funcall parse "[{\"id\": 1, \"name\": null}, {\"name\": \"\"}]") nil))))

(defconst work-kb-test--transitions-output
  (concat "jira_act: KB-9 전환 후보\n"
          "[{\"id\": \"41\", \"name\": \"Done\", \"to\": \"Done\", \"to_category\": \"done\"}, "
          "{\"id\": \"42\", \"name\": \"Done\", \"to\": \"Closed\", \"to_category\": \"done\"}, "
          "{\"id\": \"43\", \"name\": \"In Progress\", \"to\": \"In Progress\", \"to_category\": \"indeterminate\"}]\n")
  "`kb.py act transitions KB-9 --json' 출력 (mock_jira의 In Review: 이름이 같은 Done 두 개).")

(ert-deftest work-kb-test-jira-transition ()
  "t: `act transitions KEY --json' → 전환마다 한 후보 → 확인 → `act transition KEY <id> --yes'.
KB-9의 Done 둘(41 → Done, 42 → Closed)은 따로 보이고, 고른 쪽의 id가 간다."
  (work-kb-test--with-vault vault
    (let (calls)
      (work-kb-test--fake-run calls
          (lambda (args)
            (if (equal (cadr args) "transitions")
                (work-kb-test--emit "*work-kb-jira*" work-kb-test--transitions-output 0)
              0))
        (with-temp-buffer
          (insert "회의 KB-9")
          (work-kb-test--answering nil '("Done → Closed (42)") t (work-kb-jira-transition))
          (should (equal (cdr (cl-find-if #'consp work-kb-test--prompts))
                         '("Done → Done (41)" "Done → Closed (42)" "In Progress → In Progress (43)")))
          (should (cl-some (lambda (p) (and (stringp p) (string-match-p "KB-9.*'Done → Closed (42)'" p)))
                           work-kb-test--prompts))))
      (should (equal (mapcar #'car (reverse calls))
                     (list (work-kb-test--act vault "transitions" "KB-9" "--json")
                           (work-kb-test--act vault "transition" "KB-9" "42" "--yes")))))))

(ert-deftest work-kb-test-jira-transition-declined-or-failed ()
  "t: 확인에서 n이면 전환을 보내지 않는다. 목록 조회가 실패하면 묻지도 않는다."
  (work-kb-test--with-vault vault
    (let (calls)
      (work-kb-test--fake-run calls
          (lambda (_) (work-kb-test--emit "*work-kb-jira*" work-kb-test--transitions-output 0))
        (with-temp-buffer
          (insert "KB-12")
          (work-kb-test--answering nil '("In Progress → In Progress (43)") nil (work-kb-jira-transition))))
      (should (equal (mapcar #'car calls) (list (work-kb-test--act vault "transitions" "KB-12" "--json"))))
      (setq calls nil)
      (work-kb-test--fake-run calls
          (lambda (_) (work-kb-test--emit "*work-kb-jira*" "jira_act: 연결 실패\n" 3))
        (save-window-excursion
          (with-temp-buffer
            (insert "KB-12")
            (work-kb-test--answering nil nil :none
              (should-error (work-kb-jira-transition) :type 'user-error)))))
      (should (= 1 (length calls)))
      (setq calls nil)
      (work-kb-test--fake-run calls (lambda (_) (work-kb-test--emit "*work-kb-jira*" "[]\n" 0))
        (with-temp-buffer
          (insert "KB-12")
          (work-kb-test--answering nil nil :none
            (should-error (work-kb-jira-transition) :type 'user-error))))
      (should (= 1 (length calls))))))

(ert-deftest work-kb-test-jira-assign-and-worklog ()
  "a·w 인수. 잘못된 시간·빈 담당자는 보내지 않는다."
  (work-kb-test--with-vault vault
    (let (calls)
      (work-kb-test--fake-run calls (lambda (_) 0)
        (with-temp-buffer
          (insert "KB-12 작업")
          (work-kb-test--answering '("kim.cs") nil :none (work-kb-jira-assign))
          (work-kb-test--answering '("1h 30M" "배포 준비 😀") nil :none (work-kb-jira-worklog))
          (work-kb-test--answering '("45m" "") nil :none (work-kb-jira-worklog))
          (work-kb-test--answering '("2H" "") nil :none (work-kb-jira-worklog))
          ;; jira_act.parse_duration이 받지 않는 꼴(일·주·순서 뒤바뀜·빈 값·0분)은 프로세스 없이 거른다.
          (dolist (bad '("한 시간" "1d" "2w" "30m1h" "1h1h" "" "0h0m"))
            (work-kb-test--answering (list bad) nil :none
              (should-error (work-kb-jira-worklog) :type 'user-error)))
          (work-kb-test--answering '(" ") nil :none
            (should-error (work-kb-jira-assign) :type 'user-error))))
      (should (equal (mapcar #'car (reverse calls))
                     (list (work-kb-test--act vault "assign" "KB-12" "kim.cs")
                           (work-kb-test--act vault "worklog" "KB-12" "1h30m" "--comment" "배포 준비 😀")
                           (work-kb-test--act vault "worklog" "KB-12" "45m")
                           (work-kb-test--act vault "worklog" "KB-12" "2h")))))))

(ert-deftest work-kb-test-jira-project-keys ()
  "status JSON 어디에 있든 project_keys를 찾는다."
  (should (equal (work-kb--find-project-keys '((project_keys "KB" "OPS"))) '("KB" "OPS")))
  (should (equal (work-kb--find-project-keys '((vault . "x") (jira (base_url . "u") (project_keys "KB"))))
                 '("KB")))
  (should-not (work-kb--find-project-keys '((vault . "x") (outbox (pending . 0)))))
  (should-not (work-kb--find-project-keys nil)))

(defconst work-kb-test--status-json
  "{\n \"vault\": \"D:/한글 볼트\",\n \"project_keys\": [\"KB\", \"OPS\"],\n \"outbox\": {\"pending\": 0, \"failed\": 0}\n}\n")

(ert-deftest work-kb-test-jira-create ()
  "n: 프로젝트(status --json 후보)·요약·유형·설명 → 확인 → `act create … --yes'. n이면 보내지 않는다."
  (work-kb-test--with-vault vault
    (let (calls)
      (work-kb-test--fake-run calls
          (lambda (args)
            (if (equal (car args) "status")
                (work-kb-test--emit "*work-kb-status-json*" work-kb-test--status-json 0)
              0))
        (work-kb-test--answering '("새 대시보드" "") '("ops" "Bug") t (work-kb-jira-create))
        (should (equal (cdr (car (last (cl-remove-if-not #'consp work-kb-test--prompts)))) '("KB" "OPS")))
        (work-kb-test--answering '("요약" "설명 한 줄") '("KB" "Task") t (work-kb-jira-create))
        (work-kb-test--answering '("취소할 일감" "") '("KB" "Task") nil (work-kb-jira-create)))
      (let ((status (list "status" "--root" (directory-file-name vault) "--json")))
        (should (equal (mapcar #'car (reverse calls))
                       (list status
                             (work-kb-test--act vault "create" "OPS" "--summary" "새 대시보드" "--type" "Bug" "--yes")
                             status
                             (work-kb-test--act vault "create" "KB" "--summary" "요약" "--type" "Task"
                                                "--description" "설명 한 줄" "--yes")
                             status)))))))

(ert-deftest work-kb-test-jira-create-without-project-list ()
  "status에 project_keys가 없으면 직접 입력. 키 꼴이 아니면 거부."
  (work-kb-test--with-vault vault
    (let (calls)
      (work-kb-test--fake-run calls
          (lambda (args) (if (equal (car args) "status")
                             (work-kb-test--emit "*work-kb-status-json*" "{\"vault\": \"x\"}\n" 0)
                           0))
        (work-kb-test--answering nil '("한글") :none
          (should-error (work-kb-jira-create) :type 'user-error))
        (work-kb-test--answering '("요약" "") '("web" "Task") t (work-kb-jira-create)))
      (should (equal (car (car calls))
                     (work-kb-test--act vault "create" "WEB" "--summary" "요약" "--type" "Task" "--yes"))))))

(ert-deftest work-kb-test-jira-promote ()
  "p: 현재 daily 줄 → 확인 → `act promote --from daily/…:N PROJECT --yes'. 성공하면 버퍼를 다시 읽는다."
  (work-kb-test--with-vault vault
    (let ((file (expand-file-name "daily/2026-11-05.md" vault))
          (create-lockfiles nil)
          calls)
      (work-kb-test--fake-run calls
          (lambda (args)
            (pcase (car args)
              ("status" (work-kb-test--emit "*work-kb-status-json*" work-kb-test--status-json 0))
              (_ (work-kb-test--rewrite-on-disk
                  file (string-replace "경비 정산\n" "경비 정산 [[KB-100]]\n" work-kb-test--jira-daily))
                 0)))
        (save-current-buffer
          (let ((buf (work-kb-test--visit-daily vault "경비")))
            (work-kb-test--no-prompts
              (work-kb-test--answering nil '("KB") t (work-kb-jira-promote)))
            (should (string-match-p "경비 정산 \\[\\[KB-100\\]\\]" (with-current-buffer buf (buffer-string))))
            (should-not (buffer-modified-p buf)))))
      (should (equal (mapcar #'car (reverse calls))
                     (list (list "status" "--root" (directory-file-name vault) "--json")
                           (work-kb-test--act vault "promote" "--from" "daily/2026-11-05.md:12" "KB" "--yes")))))))

(ert-deftest work-kb-test-jira-promote-declined-or-outside-daily ()
  "p: 확인에서 n이면 promote를 보내지 않고 daily도 그대로. daily 밖이면 아무것도 실행하지 않는다."
  (work-kb-test--with-vault vault
    (let ((file (expand-file-name "daily/2026-11-05.md" vault))
          calls)
      (work-kb-test--fake-run calls
          (lambda (args) (if (equal (car args) "status")
                             (work-kb-test--emit "*work-kb-status-json*" work-kb-test--status-json 0)
                           0))
        (save-current-buffer
          (work-kb-test--visit-daily vault "경비")
          (work-kb-test--answering nil '("KB") nil (work-kb-jira-promote))
          (goto-char (point-max))
          (work-kb-test--answering nil nil :none
            (should-error (work-kb-jira-promote) :type 'user-error)))
        (with-temp-buffer
          (insert "- [ ] 09:30 경비 정산")
          (work-kb-test--answering nil nil :none
            (should-error (work-kb-jira-promote) :type 'user-error))))
      (should (equal (mapcar #'caar calls) '("status")))
      (should (equal (work-kb-test--bytes file) (work-kb-test--utf8 work-kb-test--jira-daily))))))

(ert-deftest work-kb-test-jira-query-sync-flush-check-done ()
  "q·S·f·d 인수와 출력 버퍼. q·d 결과 버퍼는 읽기 전용 결과 모드."
  (work-kb-test--with-vault vault
    (let ((jql "project = KB AND summary ~ \"한글\"")
          calls)
      (work-kb-test--fake-run calls
          (lambda (args)
            (pcase (car args)
              ("sync" (if (member "--jql" args)
                          (work-kb-test--emit "*work-kb-jql*" "KB-12 캐시 동기화\n" 0)
                        0))
              ("today" (work-kb-test--emit "*work-kb-check-done*" "KB-12 제안\n" 0))
              (_ 0)))
        (save-window-excursion
          (work-kb-test--answering (list jql) nil :none (call-interactively #'work-kb-jira-jql))
          (work-kb-jira-sync)
          (work-kb-jira-flush)
          (work-kb-jira-check-done)
          (should-error (work-kb-jira-jql "  ") :type 'user-error)))
      (let ((root (directory-file-name vault)))
        (should (equal (reverse calls)
                       (list (cons (list "sync" "--root" root "--jql" jql) "*work-kb-jql*")
                             (cons (list "sync" "--root" root) "*work-kb-sync*")
                             (cons (work-kb-test--act vault "flush") "*work-kb-jira*")
                             (cons (list "today" "--root" root "--check-done") "*work-kb-check-done*")))))
      (dolist (name '("*work-kb-jql*" "*work-kb-check-done*"))
        (with-current-buffer name
          (should (derived-mode-p 'work-kb-results-mode))
          (should buffer-read-only)
          (should (eq (keymap-lookup (current-local-map) "RET") 'work-kb-open-at-point)))
        (kill-buffer name)))))

(ert-deftest work-kb-test-jira-act-exit-messages ()
  "act 종료 코드: 0 완료, 3 outbox에 남김, 2 인증, 그 밖은 이유와 버퍼."
  (work-kb-test--with-vault vault
    (dolist (case '((0 . "완료") (3 . "outbox에 남겼습니다") (2 . "인증 실패") (4 . "종료 4: 설정")))
      (let (calls)
        (work-kb-test--capturing-messages msgs
          (work-kb-test--fake-run calls (lambda (_) (work-kb-test--emit "*work-kb-jira*" "jira_act: 끝\n" (car case)))
            (save-window-excursion (work-kb-jira-flush)))
          (should (cl-some (lambda (m) (string-match-p (regexp-quote (cdr case)) m)) msgs)))))
    (kill-buffer "*work-kb-jira*")))

(ert-deftest work-kb-test-jira-run-output-real-process ()
  "`work-kb--run-output'은 실제 비동기 프로세스가 끝날 때까지 기다려 (코드 . 출력)을 준다."
  (work-kb-test--with-vault vault
    (let* ((orig (symbol-function 'make-process))
           (python (work-kb-test--python))
           (code (concat "import sys, json; a = json.loads(sys.stdin.buffer.read().decode('utf-8')); "
                         "sys.stdout.buffer.write(('log\\n' + json.dumps([{'name': a[-1]}], ensure_ascii=False) + '\\n').encode('utf-8')); "
                         "sys.exit(0)")))
      (cl-letf (((symbol-function 'make-process)
                 (lambda (&rest plist)
                   (apply orig (plist-put (copy-sequence plist) :command (append python (list "-c" code)))))))
        (let ((out (work-kb--run-output (work-kb--act-args "transitions" "KB-1" "완료") "*work-kb-test-out*" 60)))
          (should (eq (car out) 0))
          (should (equal (work-kb--transition-choices (work-kb--parse-json-output (cdr out))) '(("완료" . "완료"))))))
      (kill-buffer "*work-kb-test-out*"))))

;;;; 편찬·주제 제안 (Phase 4, `g'·`a')

(defmacro work-kb-test--fresh-compile (&rest body)
  "BODY 동안 `g' 진행 표시를 새로 묶는다 (다른 시험에 새지 않게)."
  (declare (indent 0))
  `(let ((work-kb--compile-running nil)) ,@body))

(ert-deftest work-kb-test-ingest-then-compile ()
  "g: `ingest --root V' 뒤 `compile --root V'를 같은 *work-kb* 버퍼로 차례로 돌린다. 묻지 않는다."
  (work-kb-test--with-vault vault
    (work-kb-test--fresh-compile
      (let ((root (directory-file-name vault))
            calls)
        (work-kb-test--capturing-messages msgs
          (work-kb-test--fake-run calls
              (lambda (args) (work-kb-test--emit "*work-kb*" (format "%s: 끝\n" (car args)) 0))
            (save-window-excursion
              (work-kb-test--answering nil nil :none (call-interactively #'work-kb-ingest))))
          (should (cl-some (lambda (m) (string-match-p "work-kb: 편찬 완료 — compile: 끝" m)) msgs)))
        (should (equal (reverse calls)
                       (list (cons (list "ingest" "--root" root) "*work-kb*")
                             (cons (list "compile" "--root" root) "*work-kb*"))))
        (should-not work-kb--compile-running)))))

(ert-deftest work-kb-test-ingest-dry-run ()
  "C-u g: 두 단계 모두 `--dry-run'."
  (work-kb-test--with-vault vault
    (work-kb-test--fresh-compile
      (let ((root (directory-file-name vault))
            calls)
        (work-kb-test--capturing-messages msgs
          (work-kb-test--fake-run calls (lambda (_) 0)
            (save-window-excursion
              (let ((current-prefix-arg '(4)))
                (call-interactively #'work-kb-ingest))))
          (should (cl-some (lambda (m) (string-match-p "편찬 미리 보기 완료" m)) msgs)))
        (should (equal (mapcar #'car (reverse calls))
                       (list (list "ingest" "--root" root "--dry-run")
                             (list "compile" "--root" root "--dry-run"))))))))

(ert-deftest work-kb-test-ingest-failures ()
  "ingest 실패(종료 1)여도 compile은 돈다. 공개 호스트 거부(종료 5)면 compile을 건너뛴다.
compile 실패는 이유와 함께 알린다. 어느 경우든 진행 표시는 풀린다."
  (work-kb-test--with-vault vault
    (dolist (case '(((1 . 0) ("ingest" "compile") "ingest 실패(종료 1: 오류), compile 완료")
                    ((5 . 0) ("ingest") "중단 — ingest 실패(종료 5: 공개 호스트 거부). compile은 돌리지 않았습니다")
                    ((0 . 3) ("ingest" "compile") "ingest 완료, compile 실패(종료 3: 네트워크)")))
      (work-kb-test--fresh-compile
        (let ((codes (car case))
              calls)
          (work-kb-test--capturing-messages msgs
            (work-kb-test--fake-run calls
                (lambda (args) (if (equal (car args) "ingest") (car codes) (cdr codes)))
              (save-window-excursion (work-kb-ingest)))
            (should (cl-some (lambda (m) (string-match-p (regexp-quote (nth 2 case)) m)) msgs)))
          (should (equal (mapcar #'caar (reverse calls)) (nth 1 case)))
          (should-not work-kb--compile-running))))))

(ert-deftest work-kb-test-ingest-in-flight ()
  "g가 도는 중에 다시 g를 누르면 또 돌리지 않는다. 시작이 실패하면 진행 표시를 푼다."
  (work-kb-test--with-vault vault
    (work-kb-test--fresh-compile
      (let (calls)
        (cl-letf (((symbol-function 'work-kb--run)
                   (lambda (args &rest _) (push args calls) 'fake-proc)))
          (work-kb-test--capturing-messages msgs
            (should (eq (work-kb-ingest) 'fake-proc))
            (should-not (work-kb-ingest))
            (should (cl-some (lambda (m) (string-match-p "이미 진행 중" m)) msgs))))
        (should (equal (mapcar #'car calls) '("ingest")))
        (should work-kb--compile-running)))
    (work-kb-test--fresh-compile
      (cl-letf (((symbol-function 'work-kb--run)
                 (lambda (&rest _) (user-error "Python을 찾지 못했습니다"))))
        (should-error (work-kb-ingest) :type 'user-error))
      (should-not work-kb--compile-running))))

(ert-deftest work-kb-test-ingest-real-async ()
  "실제 비동기 프로세스 둘: ingest의 센티널이 compile을 시작하고, 두 출력이 한 버퍼에 차례로 남는다."
  (work-kb-test--with-vault vault
    (work-kb-test--fresh-compile
      (let* ((orig (symbol-function 'make-process))
             (python (work-kb-test--python))
             (code (concat "import sys, json; a = json.loads(sys.stdin.buffer.read().decode('utf-8')); "
                           "sys.stdout.buffer.write(('\\ub2e8\\uacc4 ' + a[0] + ' ' + a[a.index('--root') + 1]"
                           " + '\\n').encode('utf-8'))"))
             (deadline (+ (float-time) 60))
             commands)
        (when (get-buffer work-kb-output-buffer)
          (with-current-buffer work-kb-output-buffer (erase-buffer) (insert "이전 출력\n")))
        (cl-letf (((symbol-function 'make-process)
                   (lambda (&rest plist)
                     (push (plist-get plist :command) commands)
                     (apply orig (plist-put (copy-sequence plist) :command (append python (list "-c" code)))))))
          (save-window-excursion
            (should (processp (work-kb-ingest)))
            (should work-kb--compile-running)
            (while (and work-kb--compile-running (< (float-time) deadline))
              (accept-process-output nil 0.05))))
        (should-not work-kb--compile-running)
        (should (= 2 (length commands)))
        (dolist (command commands)
          (should (equal (last command 4) '("-X" "utf8" "scripts/kb.py" "--argv-stdin"))))
        (let ((root (directory-file-name vault)))
          (should (equal (with-current-buffer work-kb-output-buffer (buffer-string))
                         (concat "== ingest ==\n단계 ingest " root "\n\n== compile ==\n단계 compile " root "\n"))))))))

(defconst work-kb-test--proposals
  (concat "# 주제 제안\n\n<!-- kb:proposals:start -->\n"
          "1. 배포 자동화 — 대기\n2. 캐시 설계 — 대기\n3. 장애 대응 — 대기\n"
          "<!-- kb:proposals:end -->\n")
  "compile이 쓴 것 같은 _proposals.md (형식은 시험에 중요하지 않다).")

(ert-deftest work-kb-test-proposals-accept ()
  "a: _proposals.md를 열고 번호를 받아 확인하면 `proposals accept --root V <n>'(--yes 없음).
끝나면 제안 버퍼를 묻지 않고 다시 읽는다. `all'·`#2'도 받는다."
  (work-kb-test--with-vault vault
    (let ((file (expand-file-name "wiki/_proposals.md" vault))
          (root (directory-file-name vault))
          (create-lockfiles nil)
          calls)
      (work-kb-test--write-bytes file work-kb-test--proposals)
      (work-kb-test--capturing-messages msgs
        (work-kb-test--fake-run calls
            (lambda (args)
              (work-kb-test--rewrite-on-disk
               file (string-replace "3. 장애 대응 — 대기" "3. 장애 대응 — 승인" work-kb-test--proposals))
              (work-kb-test--emit "*work-kb*" (format "proposals: %s 승인\n" (car (last args))) 0))
          (save-window-excursion
            (work-kb-test--no-prompts
              (work-kb-test--answering '(" 3 ") nil t (work-kb-proposals)))
            (let ((buf (window-buffer (selected-window))))
              (should (file-equal-p (buffer-file-name buf) file))
              (with-current-buffer buf
                (should-not (buffer-modified-p))
                (should (string-match-p "장애 대응 — 승인" (buffer-string)))))
            (should (cl-some (lambda (p) (string-match-p "제안 3을(를) 승인할까요" p)) work-kb-test--prompts))
            (work-kb-test--answering '("ALL") nil t (work-kb-proposals))
            (should (cl-some (lambda (p) (string-match-p "모든 제안을(를) 승인할까요" p)) work-kb-test--prompts))
            (work-kb-test--answering '("#2") nil t (work-kb-proposals))))
        (should (cl-some (lambda (m) (string-match-p "제안 3 승인 완료 — proposals: 3 승인" m)) msgs)))
      (should (equal (reverse calls)
                     (list (cons (list "proposals" "accept" "--root" root "3") "*work-kb*")
                           (cons (list "proposals" "accept" "--root" root "all") "*work-kb*")
                           (cons (list "proposals" "accept" "--root" root "2") "*work-kb*")))))))

(ert-deftest work-kb-test-proposals-declined-empty-invalid ()
  "a: 비우면 열기만, n이면 보내지 않음, 번호가 아니면 사용자 오류, 실패는 이유와 함께 알린다.
파일이 없으면 묻지도 열지도 않는다."
  (work-kb-test--with-vault vault
    (let ((file (expand-file-name "wiki/_proposals.md" vault))
          calls)
      (work-kb-test--fake-run calls (lambda (_) (error "보내면 안 됨"))
        (work-kb-test--capturing-messages msgs
          (save-window-excursion
            (work-kb-test--answering nil nil :none (should-not (work-kb-proposals))))
          (should (cl-some (lambda (m) (string-match-p "_proposals.md이 아직 없습니다" m)) msgs)))
        (should-not (find-buffer-visiting file))
        (work-kb-test--write-bytes file work-kb-test--proposals)
        (save-window-excursion
          (work-kb-test--answering '("  ") nil :none (should-not (work-kb-proposals)))
          (should (file-equal-p (buffer-file-name (window-buffer (selected-window))) file))
          (work-kb-test--answering '("3") nil nil (should-not (work-kb-proposals)))
          (dolist (bad '("abc" "3,4" "-1"))
            (work-kb-test--answering (list bad) nil :none
              (should-error (work-kb-proposals) :type 'user-error)))))
      (should-not calls)
      (work-kb-test--capturing-messages msgs
        (work-kb-test--fake-run calls
            (lambda (_) (work-kb-test--emit "*work-kb*" "kb: KB_AGENT=1 세션에서는 거부\n" 7))
          (save-window-excursion
            (work-kb-test--answering '("1") nil t (work-kb-proposals))))
        (should (cl-some (lambda (m) (string-match-p "제안 1 승인 실패 (종료 7: 정책 거부)" m)) msgs)))
      (should (= 1 (length calls)))
      (should (equal (work-kb-test--bytes file) (work-kb-test--utf8 work-kb-test--proposals))))))

;;; test-work-kb.el ends here
