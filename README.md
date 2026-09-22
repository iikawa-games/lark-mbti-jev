# lark-mbti-jev

在飞书 / Lark Windows 桌面群聊的姓名旁，显示 Jev 推测的 **MBTI + 模型概率**。

本机 OCR 定位姓名，透明浮层显示标签。已有判定自动复用；同一成员在当前群新增第 **11 条有效文本**时才重新判定。

![MBTI 标签效果示意：虚构群聊中的姓名旁显示类型与概率](docs/demo.png)

> 效果图为渲染示意，人物、群名、消息和概率均为虚构。应用是本机辅助浮层，并非飞书官方插件；标签仅自己可见。

## 功能

- **姓名旁显示类型与概率**：例如 `INTP 65%`。弱证据也显示最可能的完整类型；没有文本时显示“暂无文本”。
- **字号跟随、垂直居中**：按屏幕上姓名的实际文字高度调整。状态文字占用右侧空间时，标签放到该行后方；空间不足时暂不显示。
- **滚动后自动恢复**：滚轮、触控板和拖动滚动条时先隐藏标签，停稳后重新定位。过期识别结果不会贴回旧位置。
- **持久缓存**：每人最多 1,000 条文本，每群最多 20,000 条；消息 ID 去重，重启后继续使用。第 11 条新文本触发重判，更新中保留旧标签。
- **成员详情**：查看四个维度的概率、判定样本量和新增消息数；支持补充群昵称、暂停和清除当前群缓存。

## 安装

需要 Windows 10 / 11、Python 3.11+、Windows 中文 OCR 语言组件、已配置并登录的 [lark-cli](https://github.com/larksuite/cli)，以及可用的 [TypeSafe / Jev](https://docs.typesafe.ai/) 接入。

```powershell
git clone https://github.com/iikawa0918/lark-mbti-jev.git
cd lark-mbti-jev
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

确认本机 lark-cli 登录与聊天读取权限可用：

```powershell
lark-cli auth status --json
```

### 配置 Jev

已配置本机 Jev 中转的用户可以直接复用 `JEV_BASE_URL`、`JEV_CLIENT_CONFIG` 或 `~/.agents/jev-client.json`。配置文件接受 `base_url` 字段；中转模式不会读取或转发上游 API 密钥。

直连 TypeSafe 时，在本机设置 `TYPESAFE_API_KEY`，或将 `TYPESAFE_KEY_FILE` 指向本机密钥文件。密钥文件可以只有一行密钥，也可以是一行 `TYPESAFE_API_KEY=...`。配置和密钥不要写入源码或提交到 Git。

在 PowerShell 当前会话中设置环境变量后，使用同一窗口启动：

```powershell
.\.venv\Scripts\python.exe -m feishu_mbti.app
```

配置已持久保存在本机环境或配置文件时，也可以双击 `启动飞书MBTI.vbs`。

### 开始使用

1. 在控制窗口输入群名，点击“选择群聊”。没有预设群或成员。
2. 点击“开启标签”和“在飞书打开”，让选定群聊处于前台。
3. 首次看到成员时依次分析；已有结果直接显示。双击控制窗口中的成员查看详情。

控制窗口可最小化。切到其他软件时隐藏标签；关闭控制窗口会停止识别、读取和鼠标监听。没有开机自启。

`LARK_CLI_EXE` 可指定 lark-cli 原生可执行文件的位置。程序也会尝试 PATH 和 npm 标准安装目录，不通过拼接 shell 命令执行群名或消息文本。

## 数据如何处理

| 数据 | 用途与保存位置 |
| --- | --- |
| 所选群的文本消息 | 通过现有 lark-cli 用户登录态只读获取；本机 `.data/messages.sqlite3` 缓存 |
| 分类样本 | 最近最多 200 条、40,000 字符发送到你配置的 Jev 服务；请求不附加用于身份匹配的成员姓名或账号 ID，但正文可能自然包含这些信息 |
| 判定与界面配置 | `.data/profiles.json` 和 `.data/settings.json`，仅保存在本机 |
| 屏幕图像 | Windows 本机 OCR，正常运行只在内存中处理，不上传截图 |
| 鼠标事件 | 只使用当前聊天区域的滚动时序更新浮层，不拦截操作，不保存事件记录 |

初始化读取近 90 天最多 1,000 条群消息；首次分析某成员时补拉其在同一群近 90 天最多 500 条消息。此后约每分钟同步，文本会逐步积累到缓存上限。单条缓存最多 8,000 字符。重复读取和旧历史补拉不会按新消息重复计数。

“清除缓存”删除当前群的结果与文本，并暂停标签。`.gitignore` 排除了缓存、环境文件、密钥和本机调试目录；仓库不包含任何真实群聊、成员、判定缓存或登录凭据。

## 判定与限制

一次 Jev 请求包含四个维度问题与一个 16 类型问题。标签百分比直接取候选类型分布，不是四维概率的平均值，也不会固定成 65%。这些是聊天风格推测，**模型概率不代表人格测量准确率**。

当前面向 Windows 飞书普通群聊布局。复杂昵称、话题视图和不同客户端版本可能需要适配；无法确定成员身份时不贴标签。静止时约每 1.2 秒定位一次；滚动停稳约 0.35 秒后开始重新识别，实际恢复还需要 OCR 时间。

## 开发与验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q feishu_mbti
.\.venv\Scripts\python.exe tools/render_demo.py
```

`tools/render_demo.py` 仅使用程序内的虚构数据生成 `docs/demo.png`，不会读取聊天、个人缓存或凭据，不会调用 Jev。默认使用系统中文字体；可用 `--font` 指定中文字体文件。

接口参考：[TypeSafe API](https://docs.typesafe.ai/api)、[Choice](https://docs.typesafe.ai/primitives/choice)、[Windows OCR](https://github.com/microsoft/Windows-universal-samples/tree/main/Samples/OCR)、[鼠标监听](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelmouseproc)。
