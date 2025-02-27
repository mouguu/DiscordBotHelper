# Discord论坛搜索机器人

一个功能强大的Discord机器人，专为大型服务器设计，提供高级论坛帖子搜索和内容管理功能。

## 功能特点

- **高级搜索语法**：支持AND、OR、NOT等复杂逻辑操作符
- **实时帖子搜索**：快速检索论坛帖子内容
- **分页浏览结果**：直观的界面控制，浏览大量搜索结果
- **标签过滤**：支持按标签筛选帖子
- **自动完成建议**：输入时提供智能建议
- **大型服务器优化**：专为高流量大型服务器(10000+用户)设计

## 安装说明

### 环境要求
- Python 3.11.x
- Discord.py v2.3+
- 机器人需要的权限：
  - 读取消息
  - 发送消息
  - 嵌入链接
  - 添加反应
  - 读取消息历史

### 安装步骤

1. 克隆项目仓库：
```bash
git clone https://github.com/yourusername/discord-forum-search-bot.git
cd discord-forum-search-bot
```

2. 安装依赖项：
```bash
pip install -r requirements.txt
```

3. 创建并配置环境变量文件(`.env`)：
```
DISCORD_TOKEN=your_bot_token_here
```

4. 运行机器人：
```bash
python main.py
```

## 使用指南

### 搜索命令

基本搜索：
```
/forum_search forum_name:[论坛名称] query:[搜索关键词]
```

高级搜索语法：
- AND搜索: `term1 AND term2` 或 `term1 & term2`
- OR搜索: `term1 OR term2` 或 `term1 | term2`
- NOT搜索: `NOT term` 或 `-term`
- 精确短语: `"exact phrase"`

标签过滤：
```
/forum_search forum_name:[论坛名称] tag1:[标签1] tag2:[标签2]
```

排除标签：
```
/forum_search forum_name:[论坛名称] exclude_tag1:[排除标签1]
```

### 分页控制

- ⏮️: 第一页
- ◀️: 上一页
- ▶️: 下一页
- ⏭️: 最后一页
- 🔢: 跳转到指定页面
- 🔄: 刷新结果
- ❌: 关闭搜索结果

## 大型服务器优化建议

对于拥有10000+用户和大量帖子的服务器，建议以下配置：

1. 在`config/config.py`中调整以下参数：
   - 降低 `MAX_MESSAGES_PER_SEARCH` 至合理值(如500-1000)
   - 增加 `CACHE_TTL` 至5-10分钟
   - 增加 `REACTION_TIMEOUT` 以延长会话有效期

2. 启用高级缓存设置：
   - 设置 `USE_REDIS_CACHE=True` (需要额外安装Redis)
   - 配置 `THREAD_CACHE_SIZE` 以适应服务器规模

3. 在服务器管理员设置中：
   - 限制使用机器人的频道
   - 设置合理的命令冷却时间

## 性能监控

启用内置的性能监控：
```
/bot_stats
```
查看机器人运行状态、响应时间和资源使用情况。

## 故障排除

常见问题：
- **机器人无响应**：检查TOKEN配置和网络连接
- **搜索结果为空**：确认机器人有适当的频道访问权限
- **加载缓慢**：考虑调整缓存和分页设置
- **命令错误**：查看日志获取详细错误信息

## 许可证
MIT License

## 贡献指南
欢迎贡献代码、报告问题或提出改进建议。请提交Pull Request或开Issue讨论。