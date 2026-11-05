#Requires AutoHotkey v2.0
#SingleInstance Force
; work-kb 전역 캡처 단축키 (AutoHotkey v2, 선택 사항)
;
; 어느 프로그램에 있든 단축키 한 번으로 Emacs 캡처를 연다.
;   Ctrl+Alt+Shift+C  emacsclient -n -c -e "(work-kb-capture)"                  텍스트 한 줄 캡처
;   Ctrl+Alt+Shift+V  emacsclient -n -c -e "(work-kb-capture-clipboard-image)"  클립보드 이미지 캡처
;
; 주의: 이 스크립트는 구축 PC에 AutoHotkey가 설치되어 있지 않아 실행해 보지 못했다(미확인).
;       문법은 AutoHotkey v2.0 기준으로 썼다. 처음 쓸 때 두 단축키를 한 번씩 눌러 확인한다.
;
; 준비
;   1. AutoHotkey v2를 설치한다(v1에서는 실행되지 않는다. 맨 위 #Requires가 막는다).
;   2. Emacs를 서버 모드로 띄운다. init 파일에 다음 두 줄:
;        (load "<저장소>/emacs/work-kb.el")
;        (server-start)
;      EmacsClient 경로가 틀려 실행하지 못하면 이 스크립트가 알림 창을 띄운다.
;      Emacs 서버가 떠 있지 않으면 emacsclientw가 자기 오류 창을 띄운다(이 스크립트는 그 결과를 알지 못한다).
;   3. 아래 EmacsClient 경로를 내 PC에 맞게 고친다. PATH에 있으면 이름만 써도 된다.
;      emacsclientw.exe는 emacsclient.exe와 인수가 같고 콘솔 창을 띄우지 않는다.
;   4. 이 파일을 더블클릭해 실행한다. 로그온 때 자동 실행하려면
;      Win+R → shell:startup 폴더에 이 파일의 바로 가기를 넣는다.
;
; 단축키를 고르는 이유(DECISIONS D13)
;   Win+Shift+C는 PowerToys가 쓰고 있어 Ctrl+Alt+Shift+C/V로 정했다.
;   -c는 새 프레임을 연다. 데몬으로 띄운 Emacs에는 보이는 프레임이 없을 수 있기 때문이다.
;   캡처를 마치면(C-c C-c 저장, C-c C-k 취소) C-x 5 0으로 그 프레임을 닫는다.
;
; 다른 키를 쓰려면 맨 아래 두 줄의 키 이름을 바꾼다(^ Ctrl, ! Alt, + Shift, # Win).

; ── 설정 ────────────────────────────────────────────────
; emacsclient 경로. 예: "C:\Program Files\Emacs\emacs-30.1\bin\emacsclientw.exe"
EmacsClient := "emacsclientw.exe"

; 새 프레임이 뜨면 앞으로 가져온다(Windows가 포커스를 뺏기지 않게 막는 경우 대비). 0이면 끈다.
ActivateFrame := 1

; ── 동작 ────────────────────────────────────────────────
RunEmacs(expr) {
    global EmacsClient, ActivateFrame
    command := '"' EmacsClient '" -n -c -e "' expr '"'
    try {
        Run(command)
    } catch as err {
        MsgBox("emacsclient를 실행하지 못했습니다.`n`n경로: " EmacsClient
            . "`n오류: " err.Message
            . "`n`ncapture.ahk 맨 위 EmacsClient 경로와 Emacs 서버 모드(server-start)를 확인하세요.",
            "work-kb 캡처", "Iconx")
        return
    }
    if ActivateFrame && WinWait("ahk_class Emacs", , 3)
        WinActivate()
}

^!+c::RunEmacs("(work-kb-capture)")
^!+v::RunEmacs("(work-kb-capture-clipboard-image)")
