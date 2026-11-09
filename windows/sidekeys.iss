; sidekeys Windows 安装包（Inno Setup 7）
; 构建：iscc windows\sidekeys.iss   输出到 dist\sidekeys-setup-<ver>.exe
; 按用户安装到 %LOCALAPPDATA%\Programs\sidekeys，不需要管理员权限。

#define MyAppName "sidekeys"
#define MyAppVersion "0.5.2"
#define MyAppExeName "sidekeys-win.exe"
#define MyAppId "{{8E2C7F0A-3B1D-4A6E-9C2F-5D7B1E4A9C63}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=lilyrosary-eng
AppPublisherURL=https://github.com/lilyrosary-eng/sidekeys
AppSupportURL=https://github.com/lilyrosary-eng/sidekeys
AppUpdatesURL=https://github.com/lilyrosary-eng/sidekeys/releases
DefaultDirName={localappdata}\Programs\sidekeys
DefaultGroupName=sidekeys
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=sidekeys-setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}.0
CloseApplications=yes

[Languages]
Name: "chs"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: unchecked
Name: "autostart"; Description: "开机自动启动 sidekeys"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\sidekeys"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 sidekeys"; Filename: "{uninstallexe}"
Name: "{autodesktop}\sidekeys"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
    ValueName: "sidekeys"; ValueData: """{app}\{#MyAppExeName}"" --tray"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 sidekeys"; Flags: nowait postinstall skipifsilent
