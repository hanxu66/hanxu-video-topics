# 安装与更新

## 推荐：让 Codex 安装

把下面这段话复制到自己的Codex对话中：

```text
请用 $skill-installer 从 https://github.com/hanxu66/hanxu-video-topics/tree/v0.2.1/skills/hanxu-video-topics 安装韩叙短视频选题Skill，技能名 hanxu-video-topics。请识别我的系统，保留完整目录，核对SKILL.md、references、agents和scripts。已有同名版本时，先将旧版备份到技能发现目录之外再更新，不改其他技能。告诉我实际安装位置，并检查能否识别。
```

这会选择仓库中的技能子目录，不把整个源码仓库误当技能。安装后在后续对话中调用`$hanxu-video-topics`；如果仍未出现，重新打开Codex后再试。安装目录由本机安装器确定，以助手报告的实际路径为准，不需要在多个位置重复安装。

## 手动安装：Mac 和 Windows

1. 打开[最新Release](https://github.com/hanxu66/hanxu-video-topics/releases/latest)，下载名为`hanxu-video-topics-v0.2.1.zip`的文件。GitHub自动生成的“Source code”是开发源码，目录层级不同。
2. 解压。发布包应是下面的结构，`SKILL.md`直接位于`hanxu-video-topics`之下。
3. 将整个`hanxu-video-topics`目录复制到你的本机用户技能目录，保留子目录。不要只复制`SKILL.md`，也不要多嵌套一层同名文件夹。

```text
hanxu-video-topics/
  SKILL.md
  README.md
  INSTALL.md
  USAGE.md
  LICENSE
  agents/openai.yaml
  references/...
  scripts/topic_library.py
```

按当前[OpenAI官方技能文档](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)，用户技能目录是`$HOME/.agents/skills`。常见路径：

| 系统 | 复制后的技能入口 |
|---|---|
| macOS | `~/.agents/skills/hanxu-video-topics/SKILL.md` |
| Windows | `%USERPROFILE%\.agents\skills\hanxu-video-topics\SKILL.md` |

Mac可在Finder用“前往文件夹”打开`~/.agents/skills`；Windows可在文件资源管理器地址栏输入`%USERPROFILE%\.agents\skills`。不存在时可创建该目录。若内置安装器已安装到它报告的位置，直接使用，不必为匹配表格再复制一次。

若用GitHub“Code → Download ZIP”下载源码，技能目录位于`hanxu-video-topics-main/skills/hanxu-video-topics`，复制其中这整个子目录。仓库根目录不是技能入口。

## 验证安装并开始

发给Codex：

```text
使用 $hanxu-video-topics，先确认已经读到本Skill。告诉我当前能否搜索或浏览，以及是否能保存本地文件。然后根据我提供的专业和观众开始找灵感；只问这次缺少的必要背景，不要求我先填完整配置。
```

助手应能说明这个Skill用于找灵感与补充备选库，每条包含具体回想提示。它能读到方法，不等于能登录平台或已经开始搜索。完整首次任务见[使用说明](USAGE.md)。

想保存配置和题库时，指定一个你自己的内容工作目录，例如Mac的`~/Documents/我的选题库`或Windows的`D:\内容创作\我的选题库`，让助手在其中初始化；不要存进技能安装目录。仅对话使用无需Python；本地库工具需要Python 3.9以上，Windows可由助手按本机选择`py -3`或实际可用解释器，不要求先装WSL。

## 更新与排错

| 情况 | 处理方式 |
|---|---|
| 看不到`$hanxu-video-topics` | 核对入口层级和安装器实际路径；在后续对话重试，仍未出现再重开Codex。 |
| 同名Skill出现两份 | 保留一份有效安装，将旧版备份移出所有技能发现目录；不把备份留在技能目录里。 |
| 安装器提示目标已存在 | 让助手先核对和保留旧版，再更新；不用强制覆盖或删除自己的题库。 |
| 已安装，但搜不到抖音或小红书 | 让助手说明缺的是搜索工具、正常登录还是可访问来源；可以先整理你提供的题面文本或截图，不宣称已联网检索。 |
| Python不可用 | 先在对话中使用；需要本地积累时再由助手说明安装Python的步骤。 |
| 结果仍然泛泛或偏工具 | 给具体反馈，如“多找职业选择与客户沟通，不要工具教程，每条提示我回想一次真实沟通”。 |

更新时仅替换技能文件，用户工作目录中的`profile.json`、`library.json`和反馈继续保留。已有题库可读取；新研究条目需要回想提示，旧条目缺失时显示待补，不自动改写你的选择。

安装机制参考[OpenAI官方文档](https://learn.chatgpt.com/docs/build-skills#install-curated-skills-for-local-use)。其他客户端的目录、调用与联网能力按其实际机制确认，不把本页当全部客户端的兼容承诺。
