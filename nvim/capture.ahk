#Requires AutoHotkey v2.0
#SingleInstance Force
; work-kb 전역 캡처 단축키 (AutoHotkey v2, 선택 사항) — Neovim/Neovide판
;
; 어느 프로그램에 있든 단축키 한 번으로 Neovim의 캡처를 연다.
;   Ctrl+Alt+Shift+C  :WorkKb capture                  텍스트 한 줄 캡처
;   Ctrl+Alt+Shift+V  :WorkKb capture-clipboard-image  클립보드 이미지 캡처
;
; 주의: 이 스크립트는 구축 PC에 AutoHotkey가 설치되어 있지 않아 실행해 보지 못했다(미확인).
;       문법은 AutoHotkey v2.0 기준으로 썼다. 처음 쓸 때 두 단축키를 한 번씩 눌러 확인한다.
;       확인할 것: (1) 서버가 떠 있을 때 캡처 버퍼가 Neovide 창에 뜨고 창이 앞으로 오는가,
;       (2) 아무것도 떠 있지 않을 때 Neovide가 열리고 그 안에서 캡처가 뜨는가.
;
; 동작
;   Neovim은 이름 있는 파이프 \\.\pipe\work-kb-nvim 에서 명령을 받는다. 이 파이프는
;     - nvim\bin\nvim-server.ps1 (install.ps1 -PreStart가 로그온 때 띄우는 헤드리스 서버) 또는
;     - nvim\bin\neovide-attach.ps1 의 대체 시작(neovide -- --listen <파이프>)
;   이 만든다.
;   1. 파이프가 있으면: Neovide 창이 없을 때만 neovide-attach.ps1로 창을 붙이고,
;      nvim --headless --server <파이프> --remote-send "<C-\><C-N>:WorkKb capture<CR>" 를 보낸 뒤
;      Neovide 창을 앞으로 가져온다.
;   2. 파이프가 없으면: neovide-attach.ps1을 실행해(서버가 없으니 Neovide가 파이프를 열고 새로 뜬다)
;      파이프가 생길 때까지 기다린 다음 1과 같이 명령을 보낸다.
;   --headless를 빼면 nvim 0.12.6의 --remote-send/--remote-expr 클라이언트가 TUI를 띄운다
;   (구축 PC에서 --remote-expr "1+1"을 --headless 없이 돌리면 결과 대신 터미널 제어 문자만 나왔다).
;
; 한계
;   - 파이프를 듣지 않는 다른 Neovide 창이 떠 있으면, 명령은 파이프 쪽 Neovim에 가고 앞으로 오는 창은
;     그 다른 창일 수 있다.
;   - 캡처 중(명령줄 입력 중)에 다시 누르면 <C-\><C-N>이 진행 중인 입력을 취소한다.
;
; 준비
;   1. AutoHotkey v2를 설치한다(v1에서는 실행되지 않는다. 맨 위 #Requires가 막는다).
;   2. 아래 Nvim 경로를 확인한다. scoop 심(shims\nvim.exe)보다 실제 exe가 빠르다.
;   3. 이 파일을 더블클릭해 실행한다. 로그온 때 자동 실행하려면
;      Win+R → shell:startup 폴더에 이 파일의 바로 가기를 넣는다.
;
; 단축키를 고르는 이유(DECISIONS D13): Win+Shift+C는 PowerToys가 쓰고 있어 Ctrl+Alt+Shift+C/V로 정했다.
; 다른 키를 쓰려면 맨 아래 두 줄의 키 이름을 바꾼다(^ Ctrl, ! Alt, + Shift, # Win).

; ── 설정 ────────────────────────────────────────────────
; nvim 경로. 예: EnvGet("USERPROFILE") "\scoop\apps\neovim\current\bin\nvim.exe"
Nvim := "nvim.exe"

; Neovim 서버 파이프 (nvim\bin\nvim-server.ps1, neovide-attach.ps1과 같아야 한다)
Pipe := "\\.\pipe\work-kb-nvim"

; Neovide를 붙이거나 띄우는 런처 (이 파일 옆 bin 폴더)
AttachLauncher := A_ScriptDir "\bin\neovide-attach.ps1"

; 파이프·창을 기다리는 최대 시간(초)
WaitSeconds := 10

; ── 동작 ────────────────────────────────────────────────
PipeExists() {
    global Pipe
    ; WaitNamedPipe: 파이프가 있고 받을 수 있으면 0이 아닌 값. 있지만 바쁘면 121(ERROR_SEM_TIMEOUT).
    if DllCall("WaitNamedPipeW", "Str", Pipe, "UInt", 1)
        return true
    return A_LastError = 121
}

StartNeovide() {
    global AttachLauncher
    Run('powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' AttachLauncher '"', , "Hide")
}

SendCommand(command) {
    global Nvim, Pipe
    keys := "<C-\><C-N>:" command "<CR>"
    RunWait('"' Nvim '" --headless --server ' Pipe ' --remote-send "' keys '"', , "Hide")
}

Capture(command) {
    global Nvim, Pipe, WaitSeconds
    try {
        if !PipeExists() {
            StartNeovide()
            deadline := A_TickCount + WaitSeconds * 1000
            while !PipeExists() && A_TickCount < deadline
                Sleep(100)
            if !PipeExists() {
                MsgBox("Neovim 파이프가 열리지 않았습니다: " Pipe
                    . "`n`nneovide-attach.ps1 경로와 Neovide 설치를 확인하세요.", "work-kb 캡처", "Iconx")
                return
            }
        } else if !WinExist("ahk_exe neovide.exe") {
            StartNeovide()
        }
        SendCommand(command)
    } catch as err {
        MsgBox("nvim에 명령을 보내지 못했습니다.`n`n경로: " Nvim
            . "`n오류: " err.Message
            . "`n`ncapture.ahk 맨 위 Nvim 경로를 확인하세요.", "work-kb 캡처", "Iconx")
        return
    }
    if WinWait("ahk_exe neovide.exe", , WaitSeconds)
        WinActivate()
}

^!+c::Capture("WorkKb capture")
^!+v::Capture("WorkKb capture-clipboard-image")
