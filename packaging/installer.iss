; Inno Setup script — wraps the PyInstaller one-folder build into a single
; setup.exe with a Start Menu entry, an optional desktop shortcut, and an
; uninstaller. Installs per-user by default (no administrator rights needed),
; which avoids UAC prompts and conflicts with system-wide software.
;
; Build (after `pyinstaller packaging/cclc.spec` has produced dist/<app>/):
;     iscc packaging\installer.iss
; Output: dist-installer\ELAN-Corpus-Label-Comparer-Setup.exe

#define AppName "ELAN Corpus Label Comparer"
#define AppVersion "0.1.0"
#define AppPublisher "Kai Biermeier"
#define AppExeName "ELAN Corpus Label Comparer.exe"

[Setup]
AppId={{B8E2F0A6-2C4E-4E2B-9C2E-3C1A7E5D9F10}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
; Per-user install: no admin required. Users may still elevate via the dialog.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist-installer
OutputBaseFilename=ELAN-Corpus-Label-Comparer-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Package the entire PyInstaller output folder (interpreter + Qt + app).
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
