; Folith Windows installer (NSIS 3).
;
; Packages the PyInstaller one-folder bundle (dist\qt-windows\Folith).  The
; application itself is self-contained (Qt + Tesseract + eng/chi_sim/ara), so
; this only lays the folder down, makes a Start Menu shortcut, and registers an
; uninstaller.
;
; This is a per-user install: no administrator elevation is requested, and the
; default folder is %LOCALAPPDATA%\Programs\Folith.  That keeps installation and
; removal possible for an ordinary user account.  User data lives in
; %LOCALAPPDATA%\Folith (or a folder the user chose), not in the install folder,
; so uninstalling deliberately leaves tasks, terms, memory and settings alone.
; Uninstall removes only the files this installer wrote, never the folder
; recursively.  Installing and uninstalling refuse while Folith is running.
;
; Build with:  scripts\build_qt_windows.ps1  (passes APP_VERSION, BASE, OUT_FILE)
;          or:  makensis -DAPP_VERSION=0.7.1 packaging\folith-windows.nsi
;
; scripts\check_windows_installer_roundtrip.py exercises the built installer.
;
; Keep this file saved as UTF-8 *with BOM*.  NSIS 3 decodes a BOM-less script
; using the system ANSI code page, so the Chinese product name (译页) would be
; read as garbage and the installer UI would show mojibake ("璇戝彾").

Unicode true

!include "MUI2.nsh"
!include "FileFunc.nsh"

!ifndef APP_VERSION
  !error "pass -DAPP_VERSION=<version> (scripts\build_qt_windows.ps1 reads it from pyproject.toml)"
!endif

!define APP_NAME "Folith"
!define APP_NAME_CN "Folith 译页"
!define APP_PUBLISHER "Folith"
!define APP_EXE "Folith.exe"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Folith"

!ifndef BASE
  !define BASE "${__FILEDIR__}\.."
!endif
!define BUNDLE_DIR "${BASE}\dist\qt-windows\Folith"
!define ICON_FILE "${BASE}\build\qt-windows\Folith.ico"
!ifndef OUT_FILE
  !define OUT_FILE "${BASE}\dist\installer\Folith-Setup-${APP_VERSION}-windows-x64.exe"
!endif

Name "${APP_NAME_CN}"
OutFile "${OUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\${APP_NAME}"
InstallDirRegKey HKCU "${UNINST_KEY}" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey /LANG=0 "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=0 "ProductVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=0 "FileDescription" "${APP_NAME_CN} Setup"
VIAddVersionKey /LANG=0 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=0 "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey /LANG=0 "LegalCopyright" "${APP_PUBLISHER}"

!define MUI_ICON "${ICON_FILE}"
!define MUI_UNICON "${ICON_FILE}"
!define MUI_ABORTWARNING

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "启动 ${APP_NAME_CN}"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

; A running Folith.exe cannot be opened for writing, so a failed open means the
; application is still running (or something else holds the file).  Replacing
; or removing files under a running app fails halfway, so stop first.  Silent
; runs give up with exit code 2 instead of waiting for a click.
!define RUNNING_TEXT "Folith 正在运行。请先退出 Folith，再点「重试」。$\r$\nFolith is running. Quit Folith, then click Retry."

Function RequireClosed
  IfFileExists "$INSTDIR\${APP_EXE}" 0 done
  retry:
    ClearErrors
    FileOpen $0 "$INSTDIR\${APP_EXE}" a
    IfErrors running
    FileClose $0
    Goto done
  running:
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "${RUNNING_TEXT}" /SD IDCANCEL IDRETRY retry
    SetErrorLevel 2
    Quit
  done:
FunctionEnd

Function un.RequireClosed
  IfFileExists "$INSTDIR\${APP_EXE}" 0 done
  retry:
    ClearErrors
    FileOpen $0 "$INSTDIR\${APP_EXE}" a
    IfErrors running
    FileClose $0
    Goto done
  running:
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "${RUNNING_TEXT}" /SD IDCANCEL IDRETRY retry
    SetErrorLevel 2
    Quit
  done:
FunctionEnd

Function un.onInit
  Call un.RequireClosed
FunctionEnd

Section "Folith" SecMain
  SetShellVarContext current
  Call RequireClosed
  ; Upgrading over an earlier version: drop the previous runtime folder so
  ; files the new build no longer ships do not linger next to it.  Only done
  ; when this folder already holds Folith; a foreign _internal is left alone.
  IfFileExists "$INSTDIR\${APP_EXE}" 0 +2
    RMDir /r "$INSTDIR\_internal"
  SetOutPath "$INSTDIR"
  File /r "${BUNDLE_DIR}\*.*"

  WriteUninstaller "$INSTDIR\Uninstall.exe"

  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME_CN}.lnk" "$INSTDIR\${APP_EXE}" "" \
    "$INSTDIR\${APP_EXE}" 0
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\Uninstall ${APP_NAME}.lnk" "$INSTDIR\Uninstall.exe"

  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APP_NAME_CN}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" "$\"$INSTDIR\Uninstall.exe$\""
  WriteRegStr HKCU "${UNINST_KEY}" "QuietUninstallString" \
    "$\"$INSTDIR\Uninstall.exe$\" /S"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1

  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" "$0"
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME_CN}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Uninstall ${APP_NAME}.lnk"
  RMDir "$SMPROGRAMS\${APP_NAME}"

  DeleteRegKey HKCU "${UNINST_KEY}"

  ; Remove only what the installer put here.  The install folder is user
  ; chosen and may also hold other files (or a data folder someone pointed
  ; Folith at), so never delete it recursively.  The PyInstaller bundle has
  ; exactly these two top-level entries; build_qt_windows.ps1 enforces that.
  Delete "$INSTDIR\${APP_EXE}"
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd
