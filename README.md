```markdown:README.md
# DiscordBotHelper

一个专门用于 Discord 论坛搜索和管理的 Bot，支持多样化的搜索条件和交互式结果展示。

## 主要功能

### 论坛搜索 (`/forum_search`)
- 支持多条件组合搜索：
  - 按标签搜索（最多3个标签）
  - 按关键词搜索（支持多个关键词）
  - 按发帖人筛选
  - 支持标签和发帖人排除
- 灵活的结果排序：
  - 按反应数（升序/降序）
  - 按回复数（升序/降序）
  - 按发帖时间（新到旧/旧到新）
  - 按最后活跃时间（新到旧/旧到新）
- 交互式分页显示搜索结果
  - 支持页面跳转
  - 支持首页/末页快速跳转
  - 显示帖子预览和统计信息
  - 自动处理超时和权限

### 回到顶部 (`/回顶`)
- 快速定位频道或帖子的第一条消息
- 支持普通频道、论坛帖子和线程

## 技术特性

- 异步并发处理，提高搜索效率
- 智能缓存机制，减少 API 调用
- 完善的错误处理和日志记录
- 优雅的权限检查和用户交互
- 支持持久化视图
- 模块化设计，易于扩展

## 配置项

主要配置参数（在 `config/config.py` 中）：
- `MAX_MESSAGES_PER_SEARCH`: 单次搜索的最大消息数
- `MESSAGES_PER_PAGE`: 每页显示的消息数
- `REACTION_TIMEOUT`: 交互按钮的超时时间
- `CONCURRENT_SEARCH_LIMIT`: 并发搜索限制
- `EMBED_COLOR`: 消息嵌入的颜色主题

## 环境要求

- Python 3.8+
- discord.py 2.0+
- python-dotenv

## 安装步骤

1. 克隆仓库
```bash
git clone [repository-url]
```

2. 安装依赖
```bash
pip install -r requirements.txt
```

3. 配置环境变量
创建 `.env` 文件并设置：
```env
DISCORD_TOKEN=your_bot_token
```

4. 运行 Bot
```bash
python main.py
```

## 必要的 Bot 权限

- 查看频道
- 发送消息
- 嵌入链接
- 添加反应
- 读取消息历史
- 使用外部表情符号

## 注意事项

- Bot 需要足够的权限才能正常运行
- 搜索结果会在 15 分钟后自动清理
- 只有发起搜索的用户可以操作分页按钮
- 部分功能可能需要管理员权限

## 贡献指南

欢迎提交 Issue 和 Pull Request 来帮助改进项目。

```
