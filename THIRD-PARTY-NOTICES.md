# Third-party notices / 第三方声明

本仓库自身代码（`src/`、`tools/`、`tests/`、`data/`）由作者原创编写。下面逐文件说明**依赖**与**改写来源**，
以便任何再分发都能满足各自的许可条件。

## xremap — MIT

- **运行时依赖，不随本仓库分发。** `contrib/sidekeys.service` 调用 `/usr/bin/xremap`；
  用户需自行安装 xremap（见 README 的安装一节）。版权与许可归 xremap 贡献者（MIT）。
- `contrib/sidekeys.service` —— 结构改写自 xremap 仓库的
  `doc/running_as_system_service.md` 与 `example/xremap.service`（MIT）。
  在其骨架之上，本仓库新增：`EnvironmentFile` 设备名注入、`ExecStartPre` 的 `/dev/uinput` ACL 修正、
  `--mouse` + `--device` 单设备限定、`MemoryHigh/MemoryMax`、`IPAddressDeny`。
- `contrib/99-sidekeys-uinput.rules` —— 其中一行内核规则改写自
  xremap 的 `doc/running_without_sudo.md`（MIT）；注释里补了 Ubuntu/GNOME 上单靠该规则不够的原因。
- `src/sidekeys-apply`、`src/sidekeys.py`、`src/sidekeys_validate.py`、`contrib/sidekeys-run`、`data/*` 不含来自 xremap 的代码；
  校验、语义防护、备份/回滚、设备名白名单、图形界面均为本仓库原创实现。

MIT 要求保留版权声明与许可声明，故再分发本仓库（含 `contrib/` 派生片段）时请一并保留本文件与 `LICENSE`。

## python-evdev — BSD-3-Clause

仅 `tools/omen-capture`、`tools/input-probe` 两个诊断脚本在运行时 `import evdev`。
BSD-3-Clause 与本仓库的 MIT 许可兼容。

## GTK 4 与 libadwaita — LGPL-2.1+

`src/sidekeys.py` 通过 GObject Introspection 在运行时调用系统已安装的库，
不静态链接、不复制任何库源码。按 LGPL 的一般理解，调用库不使调用方代码成为其衍生作品，
因此本仓库代码仍按 MIT 发布。

## polkit

`data/org.andeyunhui.sidekeys.policy` 遵循 freedesktop PolicyKit `policyconfig` 1.0 DTD，
属于规范样板，不含受版权保护的实现代码。

## 关于其他重映射工具

本仓库不含从 Input Remapper、keyd、kanata、evremap 等项目复制的代码。
作者在设计阶段阅读过这些项目的公开文档与 README，仅用于确认能力边界与选型，未复制其实现。
