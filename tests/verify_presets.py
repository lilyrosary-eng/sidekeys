#!/usr/bin/env python3
"""自定义预设：JSON 读写、坏数据容错、校验、以及“保存后立刻出现在所有下拉框里”。

对应 Windows 版 0.5.1 的 presets.json 功能，schema 相同（[{"name","token"}]），
只是 Linux 侧落地在 XDG 目录。
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

# 必须在 import sidekeys 之前隔离配置目录，否则会读写真实的 ~/.config/sidekeys
SANDBOX = tempfile.mkdtemp(prefix="sidekeys-presets-test-")
os.environ["XDG_CONFIG_HOME"] = SANDBOX

import sidekeys as sk  # noqa: E402
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


# ---------- 1) 路径与读写 ----------
check("presets.json 落在 XDG_CONFIG_HOME 下",
      sk.presets_path() == os.path.join(SANDBOX, "sidekeys", "presets.json"), sk.presets_path())

os.makedirs(os.path.dirname(sk.presets_path()), exist_ok=True)
with open(sk.presets_path(), "w", encoding="utf-8") as fh:
    json.dump([
        {"name": "粘贴并回车", "token": "C-v, Enter"},   # 合法
        {"name": "复制", "token": "C-c"},                 # 与内置重名 → 应跳过
        {"name": "", "token": "C-a"},                     # 空名 → 跳过
        {"name": "空映射", "token": ""},                  # 空写法 → 跳过
        "不是字典",                                        # 类型错 → 跳过
        {"name": "锁屏预设", "token": "Super-L"},          # 合法
    ], fh, ensure_ascii=False)

sk.load_user_presets()
names = [n for n, _t in sk.USER_PRESETS]
check("只加载合法条目（重名/空/类型错全部跳过）", names == ["粘贴并回车", "锁屏预设"], str(names))

with open(sk.presets_path(), "w", encoding="utf-8") as fh:
    fh.write('{"这不是": "列表"}')
sk.load_user_presets()
check("顶层不是列表时按空处理", sk.USER_PRESETS == [], str(sk.USER_PRESETS))

with open(sk.presets_path(), "w", encoding="utf-8") as fh:
    fh.write('[{ 坏掉的 json')
sk.load_user_presets()
check("文件是坏 JSON 时按空处理，不抛异常", sk.USER_PRESETS == [], str(sk.USER_PRESETS))

sk.USER_PRESETS.append(("粘贴并回车", "C-v, Enter"))
sk.USER_PRESETS.append(("锁屏预设", "Super-L"))
sk.save_user_presets()
with open(sk.presets_path(), encoding="utf-8") as fh:
    saved = json.load(fh)
check("保存成 [{name,token}] 结构（与 Windows 版一致）",
      saved == [{"name": "粘贴并回车", "token": "C-v, Enter"},
                {"name": "锁屏预设", "token": "Super-L"}], str(saved))

sk.rebuild_preset_tables()
check("用户预设进入下拉标签表", "粘贴并回车" in sk.TOKEN_BY_LABEL, str(list(sk.TOKEN_BY_LABEL)[-3:]))
check("用户预设进入 keyd 反向表（读回不会退化成自定义）",
      sk._KEYD_TO_TOKEN.get(sk.to_keyd_expr("Super-L")) == "Super-L",
      str(sk.to_keyd_expr("Super-L")))
check("内置项仍排在用户预设之前（“自定义…”永远最后）",
      sk.preset_items()[-1][0] == sk.CUSTOM_LABEL and
      [n for n, _ in sk.preset_items()][-2] == "锁屏预设",
      str([n for n, _ in sk.preset_items()][-3:]))

# ---------- 2) 界面：校验与刷新 ----------
app = Adw.Application(application_id="com.andeyunhui.sidekeys.presets.test")
state = {}


def run_case(a):
    try:
        win = sk.SideKeysWindow(application=a)
        win.present()
        state["win"] = win
        labels = [lb for lb, _ in sk.preset_items()]
        combo = win.rows[("BTN_SIDE", "tap")][0]
        model = combo.get_model()
        shown = [model.get_string(i) for i in range(model.get_n_items())]
        check("窗口里的下拉包含用户预设", "粘贴并回车" in shown and "锁屏预设" in shown, str(shown[-4:]))

        # _preset_problem：合法与非法
        accepted = win._preset_problem("我的复制", "C-S-V")
        check("合法写法被接受", accepted is None, str(accepted))
        check("名称为空被拒", win._preset_problem("", "C-c"), str(win._preset_problem("", "C-c")))
        check("空写法被拒", win._preset_problem("x", ""), str(win._preset_problem("x", "")))
        check("与内置重名被拒", win._preset_problem("复制", "C-c"), str(win._preset_problem("复制", "C-c")))
        check("与已有自定义重名被拒",
              win._preset_problem("锁屏预设", "C-c"), str(win._preset_problem("锁屏预设", "C-c")))
        check("错误修饰键被拒（keyd 只有 C/S/A/M/G）",
              win._preset_problem("y", "H-x"), str(win._preset_problem("y", "H-x")))
        check("乱造键名被真校验器拒掉",
              win._preset_problem("z", "TotallyBogus"), str(win._preset_problem("z", "TotallyBogus")))
        check("Return 被建议成 Enter",
              "Enter" in (win._preset_problem("w", "Return") or ""), str(win._preset_problem("w", "Return")))
        check("屏蔽写法 [] 被接受", win._preset_problem("屏蔽一下", "[]") is None,
              str(win._preset_problem("屏蔽一下", "[]")))
        check("宏序列写法被接受", win._preset_problem("宏", "C-c, Enter") is None,
              str(win._preset_problem("宏", "C-c, Enter")))

        # 刷新：新增后所有下拉都出现，且原选择保留
        combo.set_selected(shown.index("复制"))
        before = combo.get_selected_item().get_string()
        sk.USER_PRESETS.append(("新增测试", "F13"))
        sk.rebuild_preset_tables()
        win._refresh_preset_choices()
        combo2, _entry, labels2 = win.rows[("BTN_SIDE", "tap")]
        model2 = combo2.get_model()
        shown2 = [model2.get_string(i) for i in range(model2.get_n_items())]
        check("刷新后新预设出现在下拉里", "新增测试" in shown2, str(shown2[-4:]))
        check("刷新不改变当前选择", combo2.get_selected_item().get_string() == before,
              f"{before} → {combo2.get_selected_item().get_string()}")
        check("刷新后 rows 里存的标签表也更新了", "新增测试" in labels2, str(labels2[-4:]))
    except Exception as exc:  # 异常不许伪装成卡死
        results.append(("预设用例抛异常", False, f"{type(exc).__name__}: {exc}"))
    finally:
        a.quit()


def on_activate(a):
    run_case(a)


app.connect("activate", on_activate)
app.run(None)

failed = [r for r in results if not r[1]]
for name, ok, detail in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{str(detail)[:110]}]" if not ok and detail else ""))
print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
sys.exit(1 if failed else 0)
