# Changelog

遵循 Keep a Changelog / SemVer 风格。

## 未发布 — tools/input-probe

### 修复

- **时间戳改用事件自带的内核时间**（`ev.timestamp()`），输出列变为相对窗口内第一条事件的毫秒差。
  旧版用 `datetime.now()`（读取时刻），而 `dev.read()` 一次会排空一批事件，整批被记成同一毫秒——
  实测导致“长按到底在什么时刻发出”这类依赖间隔的判断完全无法做。
- 日志写失败（例如上一轮以 root 跑过、`/tmp/input-probe.log` 成了 root 属主）不再让程序在
  收集完数据后崩掉，自动退到 `/tmp/input-probe-<uid>.log` 并在终端提示。
- 窗口期开场白不再自带“每个键按 2 次”的节奏指示——它会覆盖调用方自己印的按键表。

## 0.5.2 — 2026-11-09

### 变更

- **开机启动改为静默进托盘**：自启命令带 `--tray`，启动后不弹主窗口，只在托盘运行并
  自动应用已保存的映射；托盘菜单“显示主窗口”照常打开界面。手动双击启动仍直接显示界面。
- 安装包的“开机自动启动”任务同步写入 `--tray`。

## 0.5.1 — 2026-11-09

### 新增

- **安装版**（Inno Setup）：`sidekeys-setup-*.exe` 按用户安装到
  `%LOCALAPPDATA%\Programs\sidekeys`，**不需要管理员权限**；提供开始菜单/桌面快捷方式、
  控制面板卸载项，卸载会清理快捷方式与开机自启。安装任务可选“开机自动启动”。
  绿色版 `sidekeys-win.exe` 继续提供，两者都在 release 里。
- **自定义预设**：界面新增“添加预设…”，填名称与映射写法（带格式范例，保存前校验重名/
  空映射/坏写法），存 `%APPDATA%\sidekeys\presets.json`；保存后立即出现在所有下拉框中。

## 0.5.0 — 2026-11-09

### 新增 — Windows 版

单文件零依赖（Tkinter + ctypes，纯标准库），后端是 `WH_MOUSE_LL` 低级鼠标钩子拦
`XBUTTON1/2`、`SendInput` 注入组合键，不需要驱动。打包为 PyInstaller 单 exe
（`sidekeys-win.exe`，无控制台）。

- 每个侧键三个手势：点按 / 长按（到点即发，阈值 100–3000ms 可调）/ 双击（200ms 窗口）。
- **双键和弦**：前后侧键一起按 / 一起长按 / 一起双击，映射成虚拟“双键”通道；
  和弦占用时取消单键待发动作，避免一起按时漏出单键原功能。
- **托盘**：关闭窗口收进托盘继续运行；右键菜单显示窗口 / 启停映射 / 退出。
- 配置存 `%APPDATA%\sidekeys\config.json`；开机启动（HKCU Run）。
- **终端友好**：注入带 `KEYEVENTF_EXTENDEDKEY`，`C-Insert`/`S-Insert` 复制粘贴在控制台生效
  （终端里 `C-c` 是终止信号，请用“复制(终端)/粘贴(终端)”预设）。
- **管理员终端**：启动时自动提权绕过 UIPI 注入拦截（本机 UAC 静默则无感）；
  托盘菜单也可“以管理员身份重启”。
- `--selftest`：8 个用例覆盖点按/长按/双击/和弦链路与解析器正负例。

## 0.4.1 — 2026-11-08

### 修复（线上事故：改了映射，按键行为却不变）

用户把前侧键长按改成 `C-a`，磁盘上的 `/etc/keyd/sidekeys.conf` 确实是新的，
但长按仍然发回车。证据链：配置文件 mtime 13:51:51，而 keyd 进程启动于 12:20:23、
最后一次 `CONFIG: parsing` 也是 12:20:24；keyd 2.5.0 源码里没有任何 inotify/watch，
**它不会自己重载配置**。

根因在助手：`apply_keyd` 用的是 `systemctl enable --now keyd`，
而这对**已经在运行**的单元是空操作 —— 配置写盘成功、服务 active、界面报“已生效”，
进程却还在用旧映射。这正是本项目一路在防的“active ≠ 生效”，结果我自己在新分支里又犯了一次。

- 新增 `bring_up()`：单元已在跑就 `restart`，没跑才 `start`；`apply_keyd` 与
  `start` 的两个后端分支全部改用它（`start` 之前也有同样的空操作问题）。
- **加重载的运行时证据**：keyd 每次加载配置都会打 `CONFIG: parsing`，
  助手现在比对重启前后的次数，没有增加就回滚并明确报“没有重新解析配置”。
  成功信息里也带上这个计数，不再只报一句 active。
- 新增 `tests/verify_helper.sh`：静态盯住这一类回归（必须走 bring_up、
  不允许再出现 `enable --now`、必须有重载证据与回滚分支）。

### 立即缓解（不改代码也能用）

```
sudo systemctl restart keyd
```

## 0.4.0 — 2026-11-07

### 新增

- **`build_deb.sh`：开箱即用的 .deb**。用 `dpkg-deb --build --root-owner-group` 直接组树，
  不需要 debhelper（`dh` 在 Ubuntu 上不是默装包）。产物 `dist/sidekeys_<版本>_amd64.deb`，
  内含 GUI、root 助手、polkit 策略、systemd 单元、udev 规则、modules-load、
  keyd 互斥 drop-in、桌面入口与文档/许可。版本号默认取 CHANGELOG 最新的数字标题。
- 构建过程自带**四项硬自检**，都是打包真正会错的地方：
  ① polkit 的 `exec.path` 必须是包内真实存在的文件；
  ② 单元的 `ExecStart` 必须是规范路径且文件在包里；
  ③ 导入包内那份 GUI 读它自己算出来的助手候选（不是在 shell 里比字符串）；
  ④ 全部条目属主 `root/root`、无组/其他可写位、无 `__pycache__`/`.pyc`。

### 变更

- **root 侧路径统一到 Debian 规范位置** `/usr/lib/sidekeys/{sidekeys-apply, sidekeys-run,
  sidekeys_validate.py}`，GUI 装到 `/usr/bin/sidekeys`；包不再往 `/usr/local` 写东西。
  polkit 策略的 `exec.path` 与单元的 `ExecStart` 同步改掉。
- GUI 不再写死助手路径：`APPLY_HELPER_CANDIDATES = (/usr/lib/sidekeys/…, /usr/local/bin/…)`
  按“第一个真实存在的”来选。旧安装升级上来的机器在重跑 install.sh 之前也能继续用，
  否则会看到一句很难懂的 polkit 授权失败。
- `install.sh` 把旧的 `/usr/local/bin/sidekeys-apply`、`/usr/local/bin/sidekeys-run`、
  `/usr/local/lib/sidekeys/sidekeys_validate.py` **改名留底**（`.pre-lib-backup`）而不是删除；
  `/etc/modules-load.d/uinput.conf` 换成我们自己的 `sidekeys-uinput.conf`，不再覆盖可能属于别的包的文件。
- `uninstall.sh` 先用 `dpkg -S` 判断：文件属于 deb 就提示改用 `sudo apt remove sidekeys`。

### 说明

- 构建脚本自身踩到并修掉的三个坑，都写进了注释：`import` 会往构建树写 `__pycache__`
  并被打进包（现在 `PYTHONDONTWRITEBYTECODE=1` + 打包前清理）；目录继承工作区得到 0775；
  `dpkg-deb -c` 的列序是 mode、uid/gid、大小，别拿错列，权限位判断要数到第 6 与第 9 位。

## 0.3.2 — 2026-11-07

### 修复（install.sh 在第 4 步就退出，root 侧根本没被更新）

用户实测：`sudo ./install.sh` 停在 `仍无法确定设备名；请手动写 /etc/xremap/sidekeys.env`，
于是助手与校验器都还是旧版，GUI 的后端守卫如实拒绝 —— 看起来就是“装了个旧版”。两个独立 bug：

- **设备名只从 `keymap` 块里找**。两个侧键都绑了长按时本工具生成的配置里**没有 keymap 块**
  （线上实测就是只有 `modmap`），于是取到空值。现在 `keymap`/`modmap`/`experimental_map` 三块都找。
- **`first_mouse()` 在这台机器上永远返回空**：本机 awk 是 mawk，`\b` 不是词边界、
  字符类里的 `[^\n]` 也不是“非换行”。兜底函数失效后，脚本就直接 exit 1。
  改成只排除空白并用 `index()` 取 `Name="…"` 的值；实测现在返回 `YICHIP Wireless Device Mouse`。
- 设备名解析失败不再中断安装：root 侧的助手/校验器/单元比这一步要紧，环境文件里的旧值仍然有效，
  现在改为打印警告并沿用原值继续。

### 说明

- 验证过 `Conflicts=` 指向**不存在**的单元不会出问题（`systemd-analyze verify` rc=0），
  所以没装 keyd 的机器上 sidekeys.service 照常可用。

## 0.3.1 — 2026-11-07

### 修复（0.3.0 的后端切换有两个真窟窿，都是用户实测发现的）

- **「启动服务」不再原地踏步**：横幅按钮过去不带后端参数，助手按“谁在跑就 start 谁”办事，
  于是选了 keyd 之后点它只是把已经在跑的 xremap 又 start 一遍，横幅文字一模一样。
  现在 `start`/`stop` 接受显式后端，并且启动一方时**显式 disable --now 另一方**。
- **状态横幅改为按“实际在跑的是哪个引擎”说话**，分四种情况：
  ① 选中的在跑 → 隐藏；② 两个都在跑 → 明确警告（实测 `device.grab()` 遇到 EBUSY 只是
  “不选中这个设备”，服务照样 running、侧键完全失灵，不会报错）；
  ③ 跑的是另一个后端 → 说“你选了 X 还没应用”，按钮改成「立即应用」；
  ④ 谁都没跑 → 才是「启动服务」。
  过去它只盯着下拉里那个选择，所以出现“明明在跑却报未运行、点启动又什么都没发生”。
- **服务快照改为带单元名的输出**：`systemctl show sidekeys keyd -p ActiveState` 在本机
  systemd 上不打印单元名（实测只输出两行 ActiveState 夹一个空行），按位置归属会在
  “少装一个/被 mask 一个”时错位报错。现在每行自带单元名与 NRestarts，一次调用问两个单元。
- **“只跑一个引擎”改成由 systemd 保证**：`contrib/sidekeys.service` 里加
  `Conflicts=keyd.service` + `Before=keyd.service`，`install.sh` 同时给 keyd 装一份
  `/etc/systemd/system/keyd.service.d/10-exclusivity.conf`（反向也声明）。
  之前只靠 apply 脚本里的 `disable --now`，堵不住“两个 unit 都被 enable、开机看谁先起”的情况。
  副作用写进 README：如果你还想让 keyd 顺便管键盘，就不能同时用 xremap 后端。
  `uninstall.sh` 会清掉这份 drop-in。

- 测试基建：`verify_ui` 的定时器回调里抛异常会让 GLib 主循环原地空转，
  表现为“测试卡死”而不是失败；现在异常会被记录并退出。`Gio.Action.activate()` 是两参数，
  之前多传了一个 platform_data。

## 0.3.0 — 2026-11-07

### 新增

- **keyd 后端**（界面里多一个「后端引擎」下拉）。存在的理由只有一个：xremap 的 `modmap` 对鼠标键
  只能在**松手时**发出长按动作，而 keyd 的定时器在它自己的事件循环里，实测长按比物理松手早
  **956ms** 就发出了。选 keyd 时生成的不再是 YAML 而是 INI：

  ```ini
  [ids]

  3151:3022

  [main]

  mouse1 = timeout(C-insert, 300, macro2(0, 0, enter))
  ```

- **「长按自动释放」开关**（keyd 后端）：开=长按动作包成 `macro2(0, 0, …)`，只发一次；
  关=裸键名，按住期间由内核重复（实测 251ms 首发、之后每约 34ms 一次，按住 1 秒 ≈ 24 个回车）。
- 写法在两个后端之间自动翻译并保证读回一致：`Super/Win → M-`、`宏序列 → macro(a b)`、
  `屏蔽 → noop`、组合 → `C-x` 形式；keyd 的键名直接问 `/usr/bin/keyd.rvaiya list-keys`。

### 变更

- **选 keyd 时“双击”那一栏被隐藏，且不会残留进配置**：keyd 没有双击算子
  （man 里“按两次”只指 layer toggle）。这是本版本明确的取舍。
- `sidekeys-apply` 现在按后端分叉，并且**保证只有一个引擎在跑**：xremap 与 keyd 都用
  `EVIOCGRAB` 独占同一只鼠标，两个都启动时后启动的那个一个事件都读不到，而且不报错。
  切到 keyd 会 `disable --now sidekeys`，切回 xremap 会 `disable --now keyd`。
  keyd 启动后额外查一次 journal 里的 `ERROR: line`——它对坏配置的态度是
  “打印错误然后继续跑”，服务 active 不代表配置生效，查到就回滚。
- `start`/`stop`/`restore` 按“谁在跑”决定作用在哪个单元；`uninstall.sh` 只有在
  `/etc/keyd/sidekeys.conf` 存在（即本工具确实接管过 keyd）时才停用它，不顺手 disable
  别人为自己键盘装的 keyd。
- 服务状态横幅按当前后端显示对应单元名（原来永远报 `sidekeys`，选 keyd 时会误报）。

### 前提与代价

- keyd 需要单独安装：`sudo apt install keyd`（Ubuntu resolute/universe 里是 2.5.0-5，
  可执行文件叫 `/usr/bin/keyd.rvaiya`）。本项目不打包它。
- `apt install keyd` / `kanata` 会**自动 enable 并启动**它们的 unit；`install.sh` 之后
  请注意 `journalctl -u keyd`，本工具的 apply 流程会显式接管这个单元。
- keyd 的 man 自陈鼠标支持“experimental”，且 `[ids]` 按 vid:pid 前缀匹配会带走该设备的
  全部接口（实测这只无线接收器的 Mouse / Consumer Control / kbd 三个接口都被接管，
  光标与笔记本键盘、触摸板均正常）。

## 0.2.5 — 2026-11-07

### 新增

- **长按判定阈值可调**（100~3000 ms，默认 400 ms）：界面多出一个「长按判定」分组，生成的
  modmap 项里写 `tap_timeout_millis`。上游默认 1000 ms 意味着“按住不足 1 秒松手都算点按”，
  对手势来说慢得离谱；读回与预览/应用三处保持一致。

### 说明（关于“长按自动释放”）

- 从 v0.15.17 源码确认：多用途键进入“已按下 hold”只能靠 repeat 事件，而**鼠标按键不发 repeat 事件**
  （上游 `event_handler.rs` 的注释明说 `BTN_RIGHT does not emit repeat events, so mouse buttons
  can't be remapped this way`），且 `Event::Tick` 在 event handler 里被显式忽略（只服务 operators）。
  所以侧键的 `held` 只能在松手时判定，届时发的是**按下+释放成对**，键不会压在手里。
  能调的只有阈值，故本版本开放它；`free_hold: true` 会让鼠标长按永远不触发，校验器直接拒绝。

### 变更

- 校验器优先使用与 GUI **同目录**的那一份：系统里的 `/usr/local/lib/sidekeys/sidekeys_validate.py`
  可能落后于 GUI 版本，会把新版合法写法（如 `tap_timeout_millis`）判成“不认识的键”。
  `install.sh` 现在也把校验器装进用户 bin 目录。

## 0.2.4 — 2026-11-07

### 修复（线上事故：点“应用”没有任何反应）

- **旧窗口不再能静默失败。** 安装是原地覆盖 `~/.local/bin/sidekeys`，已经开着的窗口仍跑着内存里的旧代码；
  而 app id 是单实例的，再执行一次 `sidekeys` 只会把激活转交给旧窗口 —— 于是新代码永远进不了场。
  这次的完整链条：旧窗口生成旧格式 YAML（`alone: S-Insert`）→ 磁盘上的新校验器按新规则拒绝 →
  只有一闪而过的 toast → 用户看到的就是“点应用无效”。
- 现在窗口每 3 秒比对自己源码文件的 sha256：与磁盘不一致就横幅提示，并提供**重启工具**按钮
  （先退场、再拉新进程，避免把激活转交给旧窗口）；此时点“应用”会明确拒绝，而不是走一遍注定被校验器拦下的提交。
- 二次启动同一个 app id 时只把已有窗口提到前面，不再叠第二个窗口。

## 0.2.3 — 2026-11-07

### 变更

- `tools/input-probe` 默认窗口期 30 → **45 秒**，新增 **5 秒预备倒计时**（先把说明读完、把手放到鼠标上），
  心跳从每 2 秒改为每 1 秒。原因：15/20 秒的窗口对人来说来不及反应，实测会出现
  “最后一次长按卡在窗口结束那一刻”甚至整轮没录到。计时本身没有 bug（实测 `--keys 15` 精确跑满 15 秒）。

## 0.2.2 — 2026-11-07

### 修复（线上事故：设了长按的侧键完全没反应）

- **`modmap` 的 `alone`/`held` 之前写成 keymap 风格的组合字符串**（`alone: S-Insert`），
  xremap 会**静默忽略**它：`--validate-config` 说 valid、服务 active、界面报“已生效”，
  但事件一个都不发。实测对照证据：同一份配置里 `BTN_SIDE: Enter`（键→键）与
  `BTN_EXTRA: {alone: [SHIFT_L, Insert], held: [CTRL_L, C]}`（数组形式）都能正常发出事件，
  而字符串形式的组合不行。现在生成器把组合拆成数组：`C-c` → `[CTRL_L, c]`。
- 校验器补上三条 modmap 专项规则：拒绝 `alone/held` 里的组合字符串（并给出数组示例）、
  拒绝数组里出现多个普通键（那是“同时按下”，不是宏序列）、放行正确数组写法。
- 读回也对称：`[CTRL_L, x]` 在界面里显示回 `C-x`。
- 双击（`experimental_map`）现在限制为**单个键名**：组合或宏序列会被界面直接拒绝。

### 说明

- 宏序列只能放在**点按**（`keymap`）里；`modmap` 的数组是“同时按下”语义，
  所以“粘贴后回车”这类序列+组合的诉求应该拆成两个手势（例如双击=回车）。
- 修饰键在 `alone/held` 数组里会一直按住到最后一个键，因此
  `[SHIFT_L, Insert, Enter]` 实际是 Shift+Enter —— 不要这么写。

## 0.2.1 — 2026-11-07

### 修复

- **“将写入的 YAML”预览框被压得很矮**：预览用的 `ScrolledWindow` 之前用
  `propagate_natural_height=True`，而它外面还套着一层可滚动容器，于是高度被算成最小值。
  现在显式设 `min=200 / max=280` 并关掉 propagate；新增回归断言“窗口压到 480px 高时
  预览框仍 >= 160px”（实测 200px）。

### 变更

- 两个 Insert 预设改名为“复制（仅部分终端绑定）/ 粘贴（仅部分终端绑定）”，
  并在 README 撤回“GTK/浏览器都认”这个过满的说法：`Ctrl+Insert` 在不少 GTK4 原生控件
  和代码编辑器里没有绑定，输入法还可能吃掉 `Shift`。默认建议仍是 `C-c`/`C-v`。

## 0.2.0 — 2026-11-07

### 新增

- **每个侧键支持三个手势**：点按 / 长按 / 双击，各自一行下拉，长按与双击默认“（不启用）”。
  点按落 `keymap`；设了长按则该键改由 `modmap` 的 `alone`/`held` 表达（不再在两层重复写）；
  双击落 `experimental_map` 的 `double` + `timeout`。
- **宏可以直接写在界面上**：自定义输入里用逗号分隔（`C-c, Enter`），生成时转成 YAML 序列，
  不再是字符串 `"C-c, Enter"`。
- `src/sidekeys_validate.py`：自建的键名与语义校验器，GUI 与 root 助手共用。
  起因是实测发现 `xremap --validate-config` **不校验 modmap 的键名**（`held: [TotallyBogus]`
  也返回 “Config is valid”），错键名会静默失效而服务仍 active；校验器会拒写并给近似建议
  （`Return` → `Enter`，`Side` → `BTN_SIDE`）。
- 测试补到四个层次：`verify_validate.py`（纯逻辑 33 项）、`verify_ui.py`（控件树 26 项）、
  `verify_apply_path.py`（应用前校验与参数，不触发授权）、`e2e_three_gestures.py`（真 apply + 撤销）。

### 变更

- 语义门放宽：只要 `keymap`/`modmap`/`experimental_map` **任一处**绑了侧键即通过。
  旧的实现只认 `keymap`，会误拒“只有长按”的合法配置。
- 预设新增“回车 / 执行”。

### 已知代价

- 双击依赖上游标注为 experimental 的 `experimental_map`，版本更新可能改配置格式；
  默认 200 ms 决策窗口会给点按动作引入同量级延迟。
- `experimental_map` 不支持 `device` 匹配，作用范围由服务的 `--device` 保证。

## 0.1.1 — 2026-11-07

### 新增

- 预设里加入 **复制（终端里也能用）`C-Insert`** 与 **粘贴（终端里也能用）`S-Insert`**。
  理由：终端模拟器把 `Ctrl+C` 当作 SIGINT、`Ctrl+V` 当作字面插入，所以默认的 `C-c/C-v` 在终端里
  本来就不该生效；PC 传统剪贴板键在 VTE/Konsole/Windows Terminal/Chromium/Firefox 里都是复制与粘贴。
  已用 `xremap --validate-config` 验证写法合法。
- README 增加“终端里 Ctrl+C 不管用——这是正确的”一节，说明两条路（传统剪贴板键 / 按应用区分），
  并明确当前 unit 是 `--desktop=none`，因此 `application:` 规则不会命中。

## 0.1.0 — 2026-11-04

第一个公开版本。

### 新增

- `src/sidekeys.py`：GTK4/libadwaita 图形界面，映射鼠标两个侧键（`BTN_SIDE` / `BTN_EXTRA`）。
  13 项预设 + 自定义 xremap 写法；实时只读 YAML 预览；服务掉线时横幅一键启动。
- `src/sidekeys-apply`：仅被 pkexec 调用的 root 侧助手，只有 `apply/restore/start/stop` 四个子命令。
  顺序为「语法校验 → 语义校验 → 备份 → 写入 → 重启 → 健康检查 → 失败自动回滚」。
- `contrib/sidekeys.service` + `contrib/99-sidekeys-uinput.rules`：以专用非 root 系统用户 `xremap` 运行，
  只接管单个鼠标设备。
- `data/org.andeyunhui.sidekeys.policy`：polkit 动作，默认 `auth_admin`（每次保存验一次身份）。
- `tools/omen-capture`、`tools/input-probe`：输入诊断工具。前者在有界窗口期内只记 press 并按
  (设备, 键码) 去重；后者同时观察 evdev 全部事件类型与 `/dev/hidraw*` 原始 HID 报告，
  用于判断某个键「根本没上报」还是「上报了但内核没映射」。
- `tests/verify_ui.py`：遍历真实控件树的结构化断言（16 项）。

### 开发过程中修掉的问题（都曾被实测复现）

- **误清空配置**：`xremap --validate-config` 对空文件是放行的，一次误操作把 `/etc/xremap/config.yml`
  覆盖成 0 字节，侧键映射被静默清除。现在助手要求暂存文件非空，且必须至少含一条 `BTN_SIDE`/`BTN_EXTRA`；
  GUI 侧同样自检。
- **注入回环**：设备枚举会把 xremap 自己创建的虚拟鼠标列出来，映射它会形成回环。现在过滤
  `Sysfs=/devices/virtual/` 的节点。
- **`/dev/uinput` 权限**：Ubuntu 上 logind 的 `uaccess` 会把 ACL 的 `group::` 降为 `---`，
  即使节点属组是 `input` 也打不开。改由 `ExecStartPre=+setfacl` 在每次启动补组 ACL，
  并实测「先清空 ACL 再重启服务」仍能起来。
- **UI 阻塞风险**：助手调用与状态查询全部走后台线程 + `idle_add`，避免复刻 Input Remapper
  那种主线程 `sleep` 轮询导致的数秒冻结。
- 若干 libadwaita API 误用（`WindowTitle` 属性名、`Banner` 的 `revealed`/`action-name`、
  `Gtk.StringList` 无 `remove_all`、`Gtk.Expander` 的 child 不进控件树）。
