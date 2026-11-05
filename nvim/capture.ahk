#Requires AutoHotkey v2.0
#SingleInstance Force
; work-kb 전역 캡처 단축키 (AutoHotkey v2, 선택 사항)
;
; UNVERIFIED (미확인): 이 PC에는 AutoHotkey가 없어 이 파일은 한 번도 실행해 보지 못했다.
;   문법은 AutoHotkey v2.0 기준으로 썼다. 처음 쓸 때 두 단축키를 한 번씩 눌러 확인한다.
;   확인할 것: (1) 데몬과 Neovide 창이 떠 있을 때 캡처가 그 창에 뜨고 창이 앞으로 오는가,
;   (2) 데몬이 없을 때 nvd가 데몬과 창을 띄우고 그 안에서 캡처가 뜨는가,
;   (3) WORK_KB_ROOT가 없거나 work-kb가 데몬에 없을 때 알림만 뜨고 아무것도 바뀌지 않는가.
;   데몬 쪽 절반(아래 Lua 조각을 nvd-rpc.lua로 보내는 것)은 tests/test_install_script.py가
;   전용 파이프의 헤드리스 nvim으로 시험한다. AutoHotkey 쪽(Run, 파일 리디렉션, 창 활성화)은 시험하지 못했다.
;
; 어느 프로그램에 있든 단축키 한 번으로 Neovim의 work-kb 캡처를 연다.
;   Ctrl+Alt+Shift+C  :WorkKb capture                  텍스트 한 줄 캡처
;   Ctrl+Alt+Shift+V  :WorkKb capture-clipboard-image  클립보드 이미지 캡처
;
; 동작 (Neovim은 nvim-config의 백그라운드 데몬이다. 이 저장소는 자체 서버를 띄우지 않는다)
;   파이프: 환경 변수 NV_PIPE, 없으면 \\.\pipe\nvim-daemon-<사용자명> (nvim-config bin\nvd.ps1과 같은 규칙).
;   1. nvd를 실행한다. 데몬이 없으면 띄우고, Neovide 창이 붙어 있으면 앞으로 가져오고, 없으면 새 창을 연다.
;      작업 폴더는 데몬의 현재 폴더로 맞춘다(데몬이 없으면 사용자 폴더). 그래야 nvd가 새 탭을 만들지 않는다.
;   2. 창(UI)이 데몬에 붙을 때까지 기다린다 (최대 WaitSeconds).
;   3. nvim-config의 bin\nvd-rpc.lua(응답이 없으면 5초 뒤 종료 코드 3)로 데몬에 Lua 조각 LuaSend를 보낸다.
;      :WorkKb 명령이 없으면(work-kb를 불러오지 않는 데몬) 'no-work-kb'를 돌려주고 아무것도 하지 않는다.
;      있으면 <C-\><C-N>:<명령><CR>(예: :WorkKb capture)을 입력 대기열에 넣는다 (삽입·명령줄 모드에서도 일반 모드로 나온 뒤 실행).
;   4. Neovide 창을 앞으로 가져온다.
;   모든 기다림에는 상한이 있다(nvd NvdWaitSeconds, RPC 한 번 RpcWaitSeconds, 창 WaitSeconds).
;   데몬이 질문(스왑 파일 등)에 걸려 있으면 RPC가 응답하지 않으므로 알림만 띄운다.
;
; 준비
;   1. AutoHotkey v2를 설치한다(v1에서는 실행되지 않는다. 맨 위 #Requires가 막는다).
;   2. nvim-config를 설치해 nvd가 PATH에 있어야 하고, 이 저장소의 install.ps1로 WORK_KB_ROOT를 정해야 한다.
;   3. 이 파일을 더블클릭해 실행한다. 로그온 때 자동 실행하려면
;      Win+R → shell:startup 폴더에 이 파일의 바로 가기를 넣는다.
;
; 단축키를 고르는 이유(DECISIONS D13): Win+Shift+C는 PowerToys가 쓰고 있어 Ctrl+Alt+Shift+C/V로 정했다.
; 다른 키를 쓰려면 맨 아래 두 줄의 키 이름을 바꾼다(^ Ctrl, ! Alt, + Shift, # Win).

; ── 설정 ────────────────────────────────────────────────
; nvd-rpc.lua를 실행할 nvim (PATH에서 찾는다)
Nvim := "nvim.exe"
; 창이 데몬에 붙기를 기다리는 최대 시간(초)
WaitSeconds := 10
; nvd 한 번(데몬 시작 포함)을 기다리는 최대 시간(초)
NvdWaitSeconds := 30
; nvd-rpc.lua 한 번을 기다리는 최대 시간(초). nvd-rpc.lua 자체가 5초 뒤 끝난다.
RpcWaitSeconds := 8

; 데몬에 보내는 Lua 조각. cmd /c 명령줄의 따옴표 안에 들어가므로 큰따옴표, %, <, >, ^를 쓰지 않는다.
; \28 \14 는 Ctrl+\ Ctrl+N, \r 은 Enter다.
LuaCwd := "return vim.fn.getcwd()"
LuaUis := "return #vim.api.nvim_list_uis()"
LuaSend := "local command = ... if vim.fn.exists(':WorkKb') ~= 2 then return 'no-work-kb' end if vim.api.nvim_get_mode().blocking then return 'busy' end vim.api.nvim_input('\28\14:' .. command .. '\r') return 'ok'"

; ── 동작 ────────────────────────────────────────────────
Notify(text) {
    TrayTip(text, "work-kb 캡처")
}

DaemonPipe() {
    pipe := EnvGet("NV_PIPE")
    return pipe != "" ? pipe : "\\.\pipe\nvim-daemon-" A_UserName
}

PipeExists(pipe) {
    ; WaitNamedPipe: 파이프가 있고 받을 수 있으면 0이 아닌 값. 있지만 바쁘면 121(ERROR_SEM_TIMEOUT). 연결은 하지 않는다.
    if DllCall("WaitNamedPipeW", "Str", pipe, "UInt", 1)
        return true
    return A_LastError = 121
}

UserEnv(name) {
    ; 이 스크립트가 시작된 뒤에 install.ps1이 정한 값도 보이도록 레지스트리에서 읽는다
    try return RegRead("HKCU\Environment", name)
    return EnvGet(name)
}

; PATH(이 프로세스의 것, 그다음 레지스트리의 사용자 PATH)에서 파일 이름 NAME을 찾는다. 없으면 "".
FindOnPath(name) {
    paths := EnvGet("PATH") ";" UserEnv("Path")
    for dir in StrSplit(paths, ";") {
        dir := Trim(dir, " `t`"")
        if (dir != "" && FileExist(dir "\" name))
            return dir "\" name
    }
    return ""
}

; nvd-rpc.lua로 데몬에 CODE를 보내고 표준 출력(앞뒤 공백 제거)을 돌려준다. 실패나 시간 초과면 "".
RpcCall(rpc, pipe, code, arg := "") {
    global Nvim, RpcWaitSeconds
    out := A_Temp "\work-kb-capture-" ProcessExist() "-" A_TickCount ".txt"
    line := A_ComSpec ' /d /s /c ""' Nvim '" -l "' rpc '" "' pipe '" "' code '" "' arg '" > "' out '" 2>&1"'
    pid := 0
    Run(line, , "Hide", &pid)
    if ProcessWaitClose(pid, RpcWaitSeconds)
        ProcessClose(pid)
    text := ""
    try text := Trim(FileRead(out, "UTF-8"), " `t`r`n")
    try FileDelete(out)
    return text
}

Capture(command) {
    global WaitSeconds, NvdWaitSeconds, LuaCwd, LuaUis, LuaSend
    if (UserEnv("WORK_KB_ROOT") = "") {
        Notify("WORK_KB_ROOT가 없습니다. work-kb 저장소의 install.ps1을 실행하세요.")
        return
    }
    nvd := FindOnPath("nvd.cmd")
    rpc := FindOnPath("nvd-rpc.lua")
    if (nvd = "" || rpc = "") {
        Notify("nvim-config의 nvd를 PATH에서 찾지 못했습니다. nvim-config의 install.ps1을 실행하세요.")
        return
    }
    pipe := DaemonPipe()

    ; 1. 창: nvd (작업 폴더 = 데몬의 현재 폴더, 그래야 새 탭이 생기지 않는다)
    cwd := EnvGet("USERPROFILE")
    if PipeExists(pipe) {
        current := RpcCall(rpc, pipe, LuaCwd)
        if (current != "" && DirExist(current))
            cwd := current
    }
    pid := 0
    Run('"' nvd '"', cwd, "Hide", &pid)
    if ProcessWaitClose(pid, NvdWaitSeconds) {
        ProcessClose(pid)
        Notify("nvd가 " NvdWaitSeconds "초 안에 끝나지 않았습니다.")
        return
    }
    if !PipeExists(pipe) {
        Notify("Neovim 데몬이 없습니다: " pipe)
        return
    }

    ; 2. 창(UI)이 붙을 때까지
    deadline := A_TickCount + WaitSeconds * 1000
    loop {
        uis := RpcCall(rpc, pipe, LuaUis)
        if (IsInteger(uis) && Integer(uis) > 0)
            break
        if (A_TickCount > deadline) {
            Notify("Neovide 창이 데몬에 붙지 않았습니다.")
            return
        }
        Sleep(200)
    }

    ; 3. 명령
    result := RpcCall(rpc, pipe, LuaSend, command)
    if (result = "no-work-kb") {
        Notify("데몬에 work-kb가 없습니다. WORK_KB_ROOT를 확인한 뒤 nvd --restart 하세요.")
        return
    }
    if (result != "ok") {
        Notify("데몬이 응답하지 않습니다. 창에서 질문을 기다리고 있는지 보세요.")
        return
    }

    ; 4. 창을 앞으로
    if WinWait("ahk_exe neovide.exe", , WaitSeconds)
        WinActivate()
}

^!+c::Capture("WorkKb capture")
^!+v::Capture("WorkKb capture-clipboard-image")
