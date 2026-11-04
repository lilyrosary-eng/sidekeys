# sidekeys

**给鼠标两个侧键做映射的图形工具**，后端是 [xremap](https://github.com/xremap/xremap)。
改动只在点“应用”时一次性写入配置并重启服务，界面全程不阻塞——这是它和 Input Remapper 最大的区别。

> **English (TL;DR).** A tiny GTK 4 / libadwaita front-end for remapping a mouse's two side buttons
> (`BTN_SIDE` / `BTN_EXTRA`) with **xremap**. No live key-recorder, no per-click round trip to a root
> daemon: edits are applied once, validated by `xremap --validate-config` plus a semantic guard, written
> with automatic backup and rollback, and the service runs as a dedicated non-root user that grabs **only
> the one mouse you pick**. Desktop-agnostic (evdev level; works on Wayland and X11). MIT licensed; see
> `THIRD-PARTY-NOTICES.md` for the two config snippets adapted from xremap's documentation.

```
后侧键 BTN_SIDE  →  复制 (C-c)
前侧键 BTN_EXTRA →  粘贴 (C-v)
```

---

## 为什么不是 Input Remapper

我做这个的直接原因是 Input Remapper 的 GUI 在这台机器上“点一个按钮卡几秒”。这不是调参能解决的，
它的代码里能看到机制：

- `gui/reader_client.py` 在 **GTK 主循环上**用 `GLib.timeout_add(30, self._read)` 轮询 root reader 子进程，
  并在 reader 掉线时用 `time.sleep(0.01)` 循环等待，上限 `_timeout = 5` 秒；上游注释自己写着
  “this will make the gui non-responsive… I would actually prefer the frozen gui over a reactive one here”。
- 一次完整设备枚举实测 **0.85 s**（这台机器有 27 个 `/dev/input/event*`）。
- 抓取失败重试是 `for attempt in range(10)` × `regrab_timeout = 0.2`，即每个忙设备最多硬等 2 秒。
- 上游有一串长期未关的同类 issue（随机冻结、注入鼠标后随机卡、组合键响应慢、混合睡眠唤醒时冻结整机）。

所以本工具刻意**不做实时按键捕获**，也刻意不在每次点击时跨进程问守护进程。代价是少了一点便利，
换来的是界面永远跟手。

---

## 依赖

| 需要 | 用途 |
|---|---|
| `xremap` 二进制（放到 `/usr/bin/xremap`） | 实际的重映射引擎。**本仓库不打包它**，许可归 xremap 贡献者（MIT） |
| GTK 4 + libadwaita + `python3-gi` + `python3-yaml` | GUI |
| `acl`（`setfacl`）、`polkit`、`systemd` | root 侧助手与提权 |
| `python3-evdev`（BSD-3） | 仅 `tools/` 里的两个诊断脚本需要 |

发行版安装 xremap 的例子（Ubuntu 26.04 的 apt 源里没有 xremap）：

```bash
curl -fsSL -o /tmp/xremap.zip \
  https://github.com/xremap/xremap/releases/download/v0.15.17/xremap-linux-x86_64-gnome.zip
cd /tmp && unzip -o xremap.zip && sudo install -m 0755 xremap /usr/bin/xremap
```

## 安装 / 卸载

```bash
sudo ./install.sh          # 服务 + GUI（幂等，不会覆盖你已有的 /etc/xremap/config.yml）
./install.sh --user-only   # 只装 GUI，先看看界面
sudo ./uninstall.sh        # 卸系统侧，保留配置
sudo ./uninstall.sh --purge
```

装完在应用列表里能看到“侧键映射 / Side Buttons”，或终端直接跑 `~/.local/bin/sidekeys`。

## 界面与用法

- **两个侧键各一行**：13 项预设（复制、粘贴、剪切、新开标签页、关闭标签页、浏览器后退/前进、刷新、
  下一个/上一个窗口、锁屏、屏蔽、自定义）。选“自定义…”会出现输入行，可直接写任意 xremap 写法。
- **作用范围**：鼠标设备下拉。设备列表来自 `/proc/bus/input/devices`（**不需要任何权限**），
  并且会过滤掉虚拟设备——包括 xremap 自己创建的输出设备，映射它会形成回环。
- **只读 YAML 预览**：保存前你能看到将要落盘的完整内容。
- **顶部横幅**：服务没跑时一键启动；标题栏副标题实时显示当前映射。

点“应用”之后依次发生：语法校验 → 语义校验 → 备份 → 写入 → 重启 → 健康检查（失败自动回滚）。

可用的写法（摘自 xremap 的 `doc/reference_actions.md` 与 `example/config.yml`）：

| 写法 | 含义 |
|---|---|
| `C-c` `A-Tab` `Shift-A-Tab` `Super-L` | 组合键 |
| `[]` | 屏蔽这个键 |
| `[C-c, A-Tab, C-v]` | 宏（键序列） |
| `[{press: B}, {sleep: 20}, {release: B}]` | 显式按下/延时/抬起 |
| `{launch: ["notify-send", "hi"]}` | 启动程序 —— **在当前部署下不要用，见“已知限制”** |

在序列前加 `throttle_ms: 10` 可以给事件之间留间隔，某些应用需要。

## 权限模型

三层，刻意分开：

1. **GUI 以普通用户运行**，只读 `/etc/xremap/config.yml`（0644）和 `/proc`，不做任何特权操作。
2. **保存时经 `pkexec` 调 `/usr/local/bin/sidekeys-apply`**（polkit action
   `org.andeyunhui.sidekeys.apply`，默认 `auth_admin`，即每次保存验一次身份）。它只认
   `apply / restore / start / stop` 四个子命令，不接受任意命令；设备名走字符白名单；
   空文件与“不含任何侧键映射”的配置一律拒写。
3. **常驻服务以专用系统用户 `xremap`（属 `input` 组）运行**，不是 root，带
   `MemoryMax=16M`、`IPAddressDeny=any`，且 `--device` 只接管你选中的那一个鼠标。

### Ubuntu 上的 `/dev/uinput` 坑

logind 的 `uaccess` 会把 `/dev/uinput` 的 ACL `group::` 降成 `---`，于是**即使节点属组是 `input`，
组成员依然打不开**（`ls -l` 看到的 `rw` 其实是 mask）。本项目的解法是
`ExecStartPre=+/usr/bin/setfacl -m g:input:rw /dev/uinput`（`+` 前缀表示该行始终以 root 执行）。
实测“先 `setfacl -b` 清掉 ACL、再重启服务”仍能正常起来。

### systemd 的引号陷阱

`ExecStart` 是**先剥引号、再展开变量**，所以 `--device "$SIDEKEYS_DEVICE"` 里的引号救不了带空格的名字——
它会被拆成四个参数（xremap 会把 `Wireless`、`Device`、`Mouse` 当成配置文件去找，报
`Failed to load config 'Wireless'…`）。因此服务的真实入口是 `contrib/sidekeys-run` 这个 wrapper，
由 shell 负责引号。这个坑是第一次真跑 `install.sh` 时暴露的，不是推演出来的。

## 已知限制（请诚实评估）

- **`launch` 类动作不可用**：服务跑在专用 `xremap` 用户下，它启动的进程不属于你的桌面会话。
  要支持“按键启动程序”需改用 xremap 的 socket 变体（`full` 包 + `xremap-<user>` 组 + 用户侧 bridge）。
- **不支持手柄**：xremap 本身没有 joystick/gamepad 支持（其文档中未出现相关能力），这不是 GUI 能补的。
- **只覆盖两个侧键**：这是个专用小工具，不是通用重映射器。多行“任意键 → 键/组合/宏”的编辑器在计划中。
- **不上报的厂商键无能为力**：例如某 HP OMEN 笔记本的六个宏键，其 HID 接口声明的是
  `Usage Page 0xFF13` 的 64 字节 INPUT/OUTPUT（主从式私有协议），按下时**该接口 0 字节输入**，
  因此 evdev 层根本看不到，任何基于 evdev 的工具（含 xremap/keyd/Input Remapper）都映射不了。
  先判断属于哪种情况，用 `tools/` 里的探针。
- **同一设备只能有一个抓取者**：如果 keyd/Input Remapper 还在抓这个鼠标，服务起不来（日志会明确报错）。
- **没有截图**：README 里暂时没有界面截图，我不想放不真实的图。欢迎提 PR。

## 输入诊断工具

```bash
pkexec tools/omen-capture 30    # 有界窗口期，只记 press、按(设备,键码)去重计数
pkexec tools/input-probe 40     # 同时看 evdev 全部事件类型与 /dev/hidraw* 原始报告
```

`input-probe` 会打印每个 hidraw 的 `vid:pid` 与接口号、每条报告的 hex 与时间戳，
并把 evdev 与 hidraw 放在同一时间轴上——用来区分“内核没映射”和“设备压根没上报”。
结果写到 `/tmp/omen-capture.log` / `/tmp/input-probe.log`。

## 开发

```
src/sidekeys.py                     GTK4/libadwaita GUI
src/sidekeys-apply                  pkexec 侧助手（校验/备份/写入/重启/回滚）
data/*.desktop.in                   桌面入口模板（install.sh 替换 @BINDIR@）
data/org.andeyunhui.sidekeys.policy polkit 动作
contrib/sidekeys.service            systemd 单元（改写自 xremap 文档，见署名）
contrib/sidekeys-run                服务真实入口，替 systemd 补足引号语义
contrib/99-sidekeys-uinput.rules    uinput 权限规则
tools/                              输入诊断
tests/verify_ui.py                  遍历真实控件树的结构化断言
```

跑测试（需要一个 GUI 会话，X11 或 Wayland 都行）：

```bash
cd tests && python3 verify_ui.py
```

它断言：分组标题齐全、下拉初值等于线上配置、设备列表不含 xremap 的虚拟输出设备、
“自定义”联动输入行可见性与预览内容、屏蔽写法生成空序列 `[]` 等。

## 许可

MIT，见 `LICENSE`。`THIRD-PARTY-NOTICES.md` 逐文件说明依赖与**改写来源**：
`contrib/` 下两个片段改写自 xremap 官方文档（MIT），再分发时请一并保留署名。
