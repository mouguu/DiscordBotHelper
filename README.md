# Discord Bot Helper

一个功能丰富的 Discord 机器人，用于帮助管理和增强您的 Discord 服务器体验。

## 功能特性

- 消息搜索功能
- 热门消息统计
- 支持分页显示的消息展示
- 线程统计和管理
- 文件附件处理
- 自定义嵌入消息展示

## 项目结构

```
DiscordBotHelper/
├── cogs/               # Discord 机器人的各个功能模块
│   ├── search.py      # 搜索相关功能
│   └── top_message.py # 热门消息统计功能
├── config/            # 配置文件目录
│   └── config.py     # 机器人配置
├── utils/            # 工具函数目录
│   ├── message_finder.py      # 消息查找工具
│   ├── pagination.py          # 分页显示工具
│   ├── thread_embed_helper.py # 线程嵌入助手
│   ├── embed_helper.py        # 消息嵌入助手
│   ├── thread_stats.py        # 线程统计工具
│   ├── helpers.py            # 通用辅助函数
│   └── attachment_helper.py  # 附件处理工具
├── main.py           # 机器人主程序
└── requirements.txt  # 项目依赖
```

## 环境要求

- Python 3.8 或更高版本
- discord.py 1.7.0 或更高版本
- python-dotenv 0.15.0 或更高版本

## 安装步骤

1. 克隆项目到本地：
```bash
git clone [项目地址]
cd DiscordBotHelper
```

2. 安装依赖：
```bash
pip install -r requirements.txt
```

3. 配置环境变量：
   - 复制 `.env.example` 文件为 `.env`
   - 在 `.env` 文件中填入您的 Discord 机器人令牌：
```
DISCORD_TOKEN=your_discord_application_token
```

## 使用方法

1. 启动机器人：
```bash
python main.py
```

2. 机器人启动后，您可以在 Discord 服务器中使用以下功能：
   - 搜索消息
   - 查看热门消息统计
   - 管理线程
   - 处理文件附件
   - 使用自定义嵌入消息

## 命令列表

具体命令列表和用法将在机器人启动后通过 Discord 的斜杠命令（/）显示。

## 许可证

本项目采用 MIT 许可证。详情请参见 [LICENSE](LICENSE) 文件。

## 贡献

欢迎提交 Issue 和 Pull Request 来帮助改进这个项目。

## 作者

mouguu

## 注意事项

- 请确保您的 Discord 机器人具有适当的权限
- 建议在使用前先在测试服务器中试用
- 定期备份重要数据
