# 工具与记录

本包不内置平台抓取服务。先检查当前AI环境实际提供的合法搜索/浏览/连接工具，再决定搜索、仅整理用户资料或明确受阻。不能把安装成功当搜索已运行，也不绑定作者电脑、登录状态或API服务。不要自动购买、安装第三方工具或新建定时任务。

优先直接读取可访问的原始作品题面；网页搜索摘要仅为发现线索，未取得原页时标范围。无法访问链接时可整理用户已给的截图/文本，未见字段保留未知。登录/验证码按平台正常流程交用户处理，不绕过；局部受阻留断点，继续其他可用来源。

## 本地可选库工具

Python 3.9以上，仅标准库。脚本无网络功能，不计算推荐分、不判断来源真假、不理解语义，操作者负责事实和状态判断。

在当前用户项目选择一个安装包之外的目录。以下路径均替换为实际路径，不要求用户逐条运行，可由AI用本地执行工具操作：

```sh
python3 /技能实际位置/scripts/topic_library.py --workspace /用户项目/短视频选题 init
python3 /技能实际位置/scripts/topic_library.py --workspace /用户项目/短视频选题 merge --input /用户项目/本轮选题.json
python3 /技能实际位置/scripts/topic_library.py --workspace /用户项目/短视频选题 list
python3 /技能实际位置/scripts/topic_library.py --workspace /用户项目/短视频选题 feedback --id 实际题目ID --quote '用户本次真实原话' --status 已选
python3 /技能实际位置/scripts/topic_library.py --workspace /用户项目/短视频选题 export
```

`profile.json`由[空白模板](profile-template.json)初始化，为本用户配置；`library.json`为唯一题库；`选题库.md`仅为展示视图，包含每题的回想提示。用户指定输出路径意味着允许保存本任务记录；否则先在对话整理，需保存时确认目录。已有目录恢复原库，不初始化覆盖。反馈命令只允许在本人确有相应原话/事实时执行，脚本无法认证原话真假。

## 导入格式

导入JSON为数组。下面是合成格式示例，不能用作真实搜索产物：

```json
[
  {
    "question_key": "自由职业者/第一次额外修改/确认工作范围",
    "topic": "第一次答应额外修改时，先确认什么",
    "audience": "刚开始接项目的自由职业者",
    "inspiration": "可以讲清额外修改怎样影响项目；具体经历由本人补充",
    "recall_prompt": "哪次客户说只改一点，你当时怎样答应，后来多做了什么？",
    "status": "储备题",
    "interest_judgment": "格式演示，真实兴趣未验证",
    "missing": ["本人材料待展开"],
    "sources": [
      {
        "platform": "user-provided-synthetic-example",
        "ref": "example:synthetic-A",
        "raw_title": "客户说只改一点，最后却重做三天（合成题面）",
        "description": null,
        "tags": null,
        "observed_at": "2026-10-22T12:00:00+08:00",
        "evidence_scope": "metadata_only",
        "metrics": {"likes": null, "followers": null},
        "discovery": {"route": "provided_materials", "query": null}
      }
    ]
  }
]
```

实际时间用真实观察时间，未知为`null`，不填0。来源`evidence_scope`可为`metadata_only`、`user_statement`、`article_text`，只反映实际读取。`ref`为链接或本地/对话可回读引用。私密凭据和登录URL不作为来源保存。

v0.2.0新导入须提供非空`recall_prompt`，即适配该用户的具体回想提示；脚本只检查字段，是否能唤起经历仍由执行者检查和用户反馈验证。旧库仍可读取与导出，缺提示显示待补；补充研究时可填入提示，不改变原选择或标题。已有提示的新建议保存在`supplements`，保留原措辞和反馈。

同核心问题复用`question_key`，脚本按该键合并；先由AI按观众任务/决策作语义判断。不同问题不能复用同键，同义标题也不能新造键。来源不同时间的观察全部保留，未知新观察不擦除旧数据。再次导入不覆盖用户的选择与措辞，新建议保存在补充记录。

将实际查询、成功/失败、覆盖、来源关系、纯联想待验证及断点保存在用户目录的本轮记录中。纯AI联想没有来源时不使用`merge`假装有证据入库。脚本原样保留扩展字段，调用方不能塞入私密凭据或把模型推测当原话。
