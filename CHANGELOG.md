# Changelog

遵循 Keep a Changelog / SemVer 风格。

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
