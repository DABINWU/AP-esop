#ifndef Payload
  #error Payload must be supplied
#endif
#ifndef Output
  #error Output must be supplied
#endif
[Setup]
AppId={{39E54B89-DF06-4840-9882-AF0AF1EF4921}
AppName=AP ESOP Workshop Relay
AppVersion=1.0.0
DefaultDirName={autopf}\AP-ESOP-Relay
DefaultGroupName=AP ESOP Relay
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#Output}
OutputBaseFilename=AP-ESOP-Relay-1.0.0-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=120,120
DisableProgramGroupPage=yes
UninstallDisplayName=AP ESOP Relay
CloseApplications=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl"

[LangOptions]
LanguageName=简体中文
LanguageID=$0804

[Messages]
SetupWindowTitle=安装 - %1
ButtonBack=< 上一步
ButtonNext=下一步 >
ButtonInstall=安装
ButtonCancel=取消
ButtonFinish=完成
WizardSelectDir=选择安装目录
SelectDirLabel3=程序文件将安装在以下目录。配置及资料单独保存，更新和卸载保留数据。
WizardReady=准备安装
ReadyLabel1=即将安装后台服务，设置开机启动。
WizardInstalling=正在安装
InstallingLabel=正在复制程序并安装后台服务，请稍候。
FinishedHeadingLabel=安装完成
FinishedLabel=中转已安装。关闭本窗口后后台服务继续运行。
[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\中转状态网页"; Filename: "{app}\状态网页.url"
Name: "{group}\卸载中转（保留数据）"; Filename: "{uninstallexe}"

[Registry]
Root: HKLM; Subkey: "Software\AP-ESOP-Relay-Installer"; Flags: uninsdeletekey

[Code]
var Conn, Admin: TInputQueryWizardPage;
    Firewall: TNewCheckBox;
    DataRoot, ServiceId, RequestFile: String;
    Existing: Boolean;

function Q(S: String): String;
begin Result := '"' + S + '"'; end;

function J(S: String): String;
begin
  StringChangeEx(S, '\', '\\', True);
  StringChangeEx(S, '"', '\"', True);
  StringChangeEx(S, #13, '', True);
  StringChangeEx(S, #10, '', True);
  Result := '"' + S + '"';
end;

function Helper(Action: String; var Code: Integer): Boolean;
var Args: String;
begin
  Args := Q(ExpandConstant('{app}\app\installer\bootstrap.py')) + ' ' + Action +
    ' --program ' + Q(ExpandConstant('{app}')) + ' --data ' + Q(DataRoot) + ' --service ' + Q(ServiceId);
  if Action = 'install' then Args := Args + ' --request ' + Q(RequestFile);
  Result := Exec(ExpandConstant('{app}\python\python.exe'), Args, '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

procedure InitializeWizard;
begin
  DataRoot := ExpandConstant('{param:DATAROOT|{commonappdata}\AP-ESOP}');
  ServiceId := ExpandConstant('{param:SERVICEID|AP-ESOP-Relay}');
  Existing := FileExists(DataRoot + '\relay.env');
  Conn := CreateInputQueryPage(wpSelectDir, '连接中心', '填写中心地址和通信密钥',
    '中转会自动后台运行。升级时留空表示保留原配置；新安装需填写中心地址及RELAY_API_KEY。');
  Conn.Add('中心地址（例如 http://192.168.0.186:18080）', False);
  Conn.Add('中心通信密钥（中心 .env 的 RELAY_API_KEY）', True);
  Conn.Add('中转编号 relay_id', False);
  Conn.Add('中转监听端口', False);
  Conn.Add('安卓终端访问密钥（留空自动生成；升级保留）', True);
  if not Existing then begin Conn.Values[0] := 'http://192.168.0.186:18080'; Conn.Values[2] := 'workshop-1'; Conn.Values[3] := '18081'; end;
  Admin := CreateInputQueryPage(Conn.ID, '中转管理员', '设置中转网页登录密码',
    '用户名为 admin。密码至少10位；升级时留空保留原密码。安卓访问密钥会安全保存在本机配置文件。');
  Admin.Add('管理员密码', True); Admin.Add('确认密码', True);
  Firewall := TNewCheckBox.Create(Admin);
  Firewall.Parent := Admin.Surface;
  Firewall.Top := Admin.Edits[1].Top + Admin.Edits[1].Height + ScaleY(16);
  Firewall.Width := Admin.SurfaceWidth; Firewall.Caption := '放行车间局域网端口（仅Private/Domain网络、LocalSubnet）';
  Firewall.Checked := True;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (ExpandConstant('{param:REQUESTFILE|}') <> '') and ((PageID = Conn.ID) or (PageID = Admin.ID));
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = Conn.ID then begin
    if not Existing and ((Length(Conn.Values[0]) = 0) or (Length(Conn.Values[1]) < 24)) then begin
      MsgBox('请填写中心地址和至少24位的中心通信密钥。', mbError, MB_OK); Result := False;
    end;
  end;
  if CurPageID = Admin.ID then begin
    if (Admin.Values[0] <> Admin.Values[1]) or ((not Existing or (Admin.Values[0] <> '')) and (Length(Admin.Values[0]) < 10)) then begin
      MsgBox('两次密码需一致，至少10位。', mbError, MB_OK); Result := False;
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer;
begin
  Result := '';
  if FileExists(DataRoot + '\installer.json') then begin
    if not Helper('stop', Code) then Result := '无法停止已有中转服务，请检查是否选择了原安装目录。';
  end else begin
    if Exec(ExpandConstant('{sys}\sc.exe'), 'query ' + Q(ServiceId), '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0) then
      Result := '已存在同名手动安装的中转服务。请先备份并停止/卸载旧服务注册，不能覆盖正在使用的中转。';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Code: Integer; Body, Source: String;
begin
  if CurStep = ssPostInstall then begin
    if not Helper('secure', Code) then RaiseException('无法保护配置目录。');
    RequestFile := DataRoot + '\install-request.json';
    Source := ExpandConstant('{param:REQUESTFILE|}');
    if Source <> '' then begin
      if not CopyFile(Source, RequestFile, False) then RaiseException('无法读取受保护的安装应答文件。');
    end else begin
      Body := '{"center_url":' + J(Conn.Values[0]) + ',"center_key":' + J(Conn.Values[1]) +
        ',"relay_id":' + J(Conn.Values[2]) + ',"port":' + J(Conn.Values[3]) +
        ',"device_key":' + J(Conn.Values[4]) + ',"admin_password":' + J(Admin.Values[0]) + ',"firewall":';
      if Firewall.Checked then Body := Body + 'true}' else Body := Body + 'false}';
      if not SaveStringToFile(RequestFile, UTF8Encode(Body), False) then RaiseException('无法保存安装配置。');
    end;
    if not Helper('install', Code) then begin
      DeleteFile(RequestFile);
      RaiseException('服务安装未完成。请检查配置、端口、已有服务和数据目录日志；请勿清空数据重试。');
    end;
    if not WizardSilent then MsgBox('安装完成，后台服务已启动并设为开机启动。' + #13#10 +
      '管理员：admin，密码为刚才设置的密码。' + #13#10 +
      '安卓访问密钥保存在 ' + DataRoot + '\relay.env 的 DEVICE_API_KEY。' + #13#10 +
      '中心离线不影响安装，VPN请另行配置。', mbInformation, MB_OK);
  end;
end;

function InitializeUninstall: Boolean;
begin
  DataRoot := ExpandConstant('{commonappdata}\AP-ESOP');
  ServiceId := 'AP-ESOP-Relay';
  // Remember actual paths in a non-secret registry key, including isolated validation installs.
  RegQueryStringValue(HKLM, 'Software\AP-ESOP-Relay-Installer', 'DataRoot', DataRoot);
  RegQueryStringValue(HKLM, 'Software\AP-ESOP-Relay-Installer', 'ServiceId', ServiceId);
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var Code: Integer;
begin
  if CurUninstallStep = usUninstall then
    if not Helper('uninstall', Code) then RaiseException('无法卸载服务，请先检查服务状态；数据保留。');
end;

procedure DeinitializeSetup;
begin
  if FileExists(DataRoot + '\installer.json') then begin
    RegWriteStringValue(HKLM, 'Software\AP-ESOP-Relay-Installer', 'DataRoot', DataRoot);
    RegWriteStringValue(HKLM, 'Software\AP-ESOP-Relay-Installer', 'ServiceId', ServiceId);
  end;
end;
