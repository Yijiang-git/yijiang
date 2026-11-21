; 译匠 v0.2 标准安装器
#define MyAppName "译匠（免费版）"
#define MyAppVersion "0.2"
#define MyAppExeName "Yijiang.exe"

[Setup]
AppId={{D4444444-5555-6666-7777-888888888888}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName=译匠 v{#MyAppVersion}
AppPublisher=译匠
DefaultDirName={localappdata}\Programs\YijiangFree
DisableDirPage=no
UsePreviousAppDir=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Yijiang-Free-Setup
Compression=lzma2
SolidCompression=no
UninstallDisplayName=译匠（免费版）
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=resources\yijiang.ico

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Files]
Source: "..\build_installer\Yijiang_free\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\译匠（免费版）"; Filename: "{app}\{#MyAppExeName}"
Name: "{userdesktop}\译匠（免费版）"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动译匠"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\YijiangFree');
    if DirExists(DataDir) then
    begin
      if MsgBox('是否同时删除本机数据？' + #13#10 + #13#10 +
                '包括翻译历史、API Key、自建规则与授权状态：' + #13#10 + DataDir + #13#10 + #13#10 +
                '选择“否”将保留这些数据，方便以后重装继续使用。',
                mbConfirmation, MB_YESNO) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
