# Changelog

遵循 Keep a Changelog / SemVer 风格。

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
