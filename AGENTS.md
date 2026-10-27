# AGENTS.md —— 这个仓库怎么维护

> 给**接手这个仓库的 AI**（或未来改它的我）看的。
> 人也能看，但重点是：**什么能传、什么不能传、改完怎么推。**

---

## 这是什么

「让别人家的小鱼也能打《杀戮尖塔》」的入门包。

一个 AI（小鱼）在 Windows 上，通过 CommunicationMod 的通信桥，**看见**游戏状态、**真的出牌**。
这个仓库就是那套东西：桥 + 眼睛 + 手 + 一份手册。

**不是教程，是能跑的东西。** 每一条都在真机上验过。

---

## 目录结构

```
.
├── README.md          ← GitHub 首屏渲染的就是它（必须叫 README.md）
├── AGENTS.md          ← 你正在读的这份
├── 给小鱼的话.md       ← 给「另一个 AI」的交接单（不是给人看的教程）
│
├── bridge.py          ← 管道。CommunicationMod 调它，别动
├── st.py              ← 眼睛。读 state.json，翻译成人话
├── st_go.py           ← 手。发命令给游戏
├── manual_hint.py     ← 每次 st.py 自动从 manual/ 查相关几行贴出来
│
└── manual/            ← 29 篇打法笔记（st.py 会按局面自动挑）
    ├── 00-开打前必读.md
    ├── 01 ~ 24 ...
    └── 尖塔数据-*.md
```

**四个 .py 是代码，`manual/` 是数据。** 两者要配套：`manual_hint.py` 靠 `manual/` 里的
文件名和小标题做匹配，**改标题会让匹配失效**。

---

## 上传规则（最重要）

### ✅ 可以上传

| 类型 | 例子 |
|---|---|
| 四个 .py | `bridge.py` / `st.py` / `st_go.py` / `manual_hint.py` |
| 手册 | `manual/*.md` |
| 给人看的说明 | `README.md` / `给小鱼的话.md` / `AGENTS.md` |
| 配置模板 | `.editorconfig` / `.gitignore` |

### ❌ 绝对不能上传

| 类型 | 为什么 |
|---|---|
| `state.json` / `state.txt` | 游戏状态，含当前局的全部信息，每天在变 |
| `bridge.log` / `cmd.txt` | 运行日志，含时间戳和每次操作 |
| `*.png` / `*.jpg`（截图） | 屏幕截图可能带桌面、其他窗口、聊天记录 |
| `work/` 目录 | `st.py` 运行时自己建的工作目录 |
| `__pycache__/` / `*.pyc` | 编译缓存，没意义 |
| 任何 token / key / cookie | 见下面「隐私红线」 |
| 本机绝对路径 | `C:\Users\<你的名字>` / `D:\某个私密目录` |

**一句话判据**：**这份文件如果只在我这台机器上有意义，就不传。**

### 判断流程

改完文件，先问三句：

1. 里面有**我这台机器的绝对路径**吗？（`C:\Users\...`、`E:\...`）
2. 里面有**运行时的数据**吗？（state / 日志 / 截图 / 存档）
3. 里面有**能认出我这个人**的东西吗？（真实姓名 / 邮箱 / 账号 / 手机）

**任何一句答「是」→ 不传，或者先脱敏。**

---

## 隐私红线

这个仓库是 **public**。下面这些一个都不能出现：

- 真实姓名、邮箱、手机号、身份证
- GitHub / B站 / 任何平台的 token、cookie、API key
- 本机用户名（`C:\Users\<名字>` 里的那部分）
- 私人对话的摘录（尤其是带名字的）
- 别人的个人信息

**历史遗留检查**：改完跑一遍

```
grep -rn "Users\\\\" --include=*.md --include=*.py .
grep -rn "E:\\\\" --include=*.md --include=*.py .
grep -rnE "gho_|ghp_|sk-" --include=*.md --include=*.py .
```

三条都该**没有输出**（除了 `.gitignore` 里故意写的规则）。

---

## 维护流程

### 改完东西，怎么推

桌面上有个 `push.py`（在 `github\` 目录下）：

```
python C:\Users\Administrator\Desktop\github\push.py "改了什么"
```

它会：`git add -A` → `commit` → 临时塞 token → `push` → 把 token 撤掉。

**为什么要临时塞 token**：本机代理是 `socks5://127.0.0.1:10808`，
而 `gh` 的凭据助手不支持 socks5，两者冲突。所以只能每次临时塞。

### 手动推（如果 push.py 不在）

```
# 1. 配代理（你的机器情况可能不同）
git config --global http.proxy  socks5://127.0.0.1:10808
git config --global https.proxy socks5://127.0.0.1:10808

# 2. 提交
git add -A
git commit -m "改了什么"

# 3. 推（需要 token，见 push.py）
git push -u origin main
```

### 改手册要注意

`manual_hint.py` 靠**文件名 + 小标题**做匹配。如果你：

- **改文件名** → 匹配可能失效，改完跑一次 `python st.py`，看手册提示还出不出来
- **改小标题** → `manual_hint.py` 里的 `ALIAS` 表可能要同步改
- **加新怪物** → 在 `ALIAS` 里加上「英文名 → 中文名」的映射

**验证方法**：`python st.py`，看输出最后有没有「手册提示」那几行。

---

## 代码约定

- **纯 Python，不装第三方包。** 只用标准库 + Windows 自带的 `ctypes`。
  加了依赖就等于给使用者加了一道门槛，能不加以不加。
- **路径全部可配置。** 三个环境变量：`STS_BRIDGE_DIR` / `STS_WORK_DIR` / `STS_MANUAL_DIR`。
  不设就用默认（桥目录用 `C:\sts_bridge`）。
- **出错要有话说。** 不要 `except: pass`。使用者是 AI 或新手，
  报错信息要能告诉他「下一步查什么」。
- **中文注释可以有，但别写私人内容。** 技术决策、踩坑记录都欢迎；
  「某某人说」「我们之间」这类不要。

---

## 一句话

**这个仓库是为「别人家的小鱼」准备的。**
每次改动，问自己：**一个从没见过这台机器的 AI，拿到它能不能跑起来？**

不能，就还没改完。
