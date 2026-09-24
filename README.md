# lark-mbti-jev

一个用于 Windows 飞书 / Lark 群聊的小工具。它让 Jev 根据聊天记录推测成员的 MBTI，并在发言者姓名旁显示结果，例如 **ENTJ 68%**，也可以换成动漫角色，例如 **丰川祥子 68%**。标签只在你的电脑上显示。

<table>
  <tr>
    <td><img src="docs/demo-mbti.png" alt="姓名旁显示 MBTI 和概率"></td>
    <td><img src="docs/demo-girl-bands.png" alt="姓名旁显示少女乐队角色和概率"></td>
  </tr>
  <tr>
    <td align="center">默认：MBTI 类型</td>
    <td align="center">少女乐队主题：同一类型对应的角色</td>
  </tr>
</table>

> 图中消息是《BanG Dream! It's MyGO!!!!!》第 13 集睦和祥子的原对话（中文译文），概率为演示数值。本工具由第三方开发，独立于飞书运行。

## 安装和启动

使用前需要准备：

- Windows 10 / 11 和 Python 3.11+。
- 已配置并登录的 [lark-cli](https://github.com/larksuite/cli)，以及读取所选群聊的权限。
- 可用的 Jev 服务，或用于直连 [TypeSafe](https://docs.typesafe.ai/) 的 API 密钥。
- Windows 中文 OCR 语言组件（仅截图兼容模式需要）。

```powershell
git clone https://github.com/iikawa-games/lark-mbti-jev.git
cd lark-mbti-jev
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
lark-cli auth status --json   # 确认 lark-cli 已登录
```

程序在 PATH 和 npm 默认安装目录中查找 lark-cli；装在其他位置时，用 `LARK_CLI_EXE` 指定路径。

### 配置 Jev

- **Jev 中转服务**：程序读取 `JEV_BASE_URL`、`JEV_CLIENT_CONFIG` 或 `~/.agents/jev-client.json`。配置文件中的 `base_url` 填服务根地址，例如 `http://127.0.0.1:8766`；API 密钥由中转服务管理。
- **直连 TypeSafe**：设置 `TYPESAFE_API_KEY`，或用 `TYPESAFE_KEY_FILE` 指向一个本机文件（一行密钥，或一行 `TYPESAFE_API_KEY=...`）。

配置保存在用户环境变量或本机文件中时，双击 `启动飞书MBTI.vbs` 即可。只在当前 PowerShell 窗口设置的变量，需要在同一窗口启动：

```powershell
$env:TYPESAFE_API_KEY = "替换为你的 TypeSafe API 密钥"
.\.venv\Scripts\python.exe -m feishu_mbti.app
```

### 选择群聊

1. 在控制窗口输入群名，点击“选择群聊”。
2. 从搜索结果中选中目标群，点击“使用此群”。
3. 点击“开启标签”，再点击“在飞书打开”。

选过群之后，双击 `启动飞书MBTI.vbs` 只在系统托盘显示图标并直接开启标签。单击图标打开控制窗口，关闭窗口后程序继续在托盘运行；右键菜单可以暂停或开启标签、刷新聊天、切换标签显示，选“退出”才会停止。程序不会开机自启。

## 标签怎么显示

程序优先通过 Windows 无障碍接口读取飞书桌面端的姓名和位置，不截图；客户端没有提供可用接口时，自动改用截图识别。控制窗口会显示当前方式。

- **状态**：还没有结果时显示“分析中”。已缓存此人文字时约 1 秒出结果，之后补读更多历史再更新一次；屏幕上的成员优先。信息较少时仍显示最可能的类型；没有可用文本显示“暂无文本”；分析失败显示“分析失败”，约 1 分钟后再看到此人时自动重试。
- **位置**：标签紧贴姓名，字号略小于姓名；姓名后有签名或状态时，标签盖住签名开头。
- **滚动**：标签跟着姓名一起移动，滚进视野的姓名随之出现标签，移出消息区时隐藏。截图兼容模式无法跟随，滚动时暂时隐藏。
- **截图和共享**：标签会出现在截图和屏幕共享里。
- **切换窗口**：只有选定群聊处于前台时才显示。

在控制窗口中双击成员，可以查看四个维度的概率、参考的消息数，以及上次分析后新增的消息数。

姓名显示为群昵称，或群里有同名成员时，程序用名字下方的消息文字确认是谁，并记住这个昵称。仍识别不准时，选中该成员，点击“补充群昵称”，填入此人在该群的完整昵称。

## 动漫角色主题

在控制窗口或托盘菜单的“标签显示”中，可以把 MBTI 换成动漫角色。百分比不变，仍是模型对该 MBTI 类型给出的概率；双击成员可以看到对应的角色、作品和类型。

内置主题：

- **魔法少女**：美少女战士、魔卡少女樱、魔法少女小圆、魔法少女奈叶、魔法少女伊莉雅、魔法纪录
- **少女乐队**：孤独摇滚！、轻音少女、BanG Dream!、MyGO!!!!!、Ave Mujica、Girls Band Cry
- **修仙者**：凡人修仙传、仙逆、诛仙、魔道祖师、天官赐福、剑来、斗破苍穹、斗罗大陆、一念永恒、完美世界、牧神记

类型与角色的对应是粉丝向的娱乐归类，不代表作品的官方设定。

**自定义主题**：点击“自定义主题…”，程序打开本机 `.data/themes` 文件夹，里面有一个示例文件。复制并改名（文件名不要以 `_` 开头），为 16 种类型填写角色，保存后即可在“标签显示”中选择。没填的类型继续显示 MBTI。

```json
{
  "name": "主题名",
  "characters": {
    "INTP": {"name": "角色名", "series": "作品名"}
  }
}
```

## 结果何时更新

同一成员再次出现时直接显示上次结果。该成员在同一群中累计新增 **11 条文本消息**后，下次显示时重新分析，期间继续显示旧结果。

开启标签后约每分钟检查一次新消息，也可以点击“刷新聊天”。点击“清除缓存”会删除当前群保存的消息和结果，并暂停标签。

## 会读取和保存哪些数据

| 数据 | 处理方式 |
| --- | --- |
| 群聊消息 | 通过 lark-cli 读取所选群，保存到本机 `.data/messages.sqlite3`。首次读取近 90 天最多 1,000 条；首次分析某成员时再查此人近 90 天最多 500 条。每人最多保留 1,000 条、每群 20,000 条、单条 8,000 字符，超出时保留较新的 |
| 发给 Jev 的文本 | 对应成员最近最多 200 条、40,000 字符，连同消息时间发送到你配置的服务。程序不另外附上成员姓名和账号 ID；正文中出现的这些信息会随文本发送 |
| 分析结果和设置 | 保存到本机 `.data/profiles.json`、`.data/settings.json`，包括成员姓名、账号 ID、所选群和手动填写的昵称 |
| 屏幕图像 | 无障碍模式不截图；兼容模式只在内存中识别，不保存或上传 |
| 鼠标操作 | 只检测当前聊天区域的滚动和拖动，用于移动或隐藏标签；操作照常传给原窗口，不保存记录 |

缓存、密钥和本机调试目录均已加入 `.gitignore`，仓库只包含程序、测试和演示数据。

## 如何理解结果

Jev 同时判断四个 MBTI 维度，并比较 16 种类型。`INTP 65%` 表示模型在本次分析中给 INTP 的概率为 65%，会随聊天内容变化。结果只反映一个人在群聊中的表达风格，**本工具没有经过人格测量准确率验证。**

目前适配 Windows 飞书的普通群聊。话题视图、复杂昵称或不同客户端布局可能影响识别；无法确定成员身份时，标签保持隐藏。

## 开发

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q feishu_mbti
.\.venv\Scripts\python.exe tools/render_demo.py
```

`tools/render_demo.py` 用上面的动画对话生成 `docs/demo-mbti.png`（MBTI）和 `docs/demo-girl-bands.png`（少女乐队主题），不读取个人聊天，也不调用 Jev。默认使用系统中文字体，可用 `--font` 指定。

接口参考：[TypeSafe API](https://docs.typesafe.ai/api)、[Choice](https://docs.typesafe.ai/primitives/choice)、[Windows OCR](https://github.com/microsoft/Windows-universal-samples/tree/main/Samples/OCR)、[鼠标监听](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelmouseproc)。
