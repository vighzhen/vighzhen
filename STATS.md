# 主页统计说明

统计直接取自 GitHub 官方 API，包含本人账号的公开及私有项目。生成的图片由本账号的 GitHub Pages 托管，深色、浅色卡片根据浏览器主题自动切换。公开页面只展示合计，不包含私有仓库名称、链接或源码。

## 更新与实时性

- 计划每 5 分钟运行一次，错开整点；也可以在[更新主页统计](https://github.com/vighzhen/vighzhen/actions/workflows/profile-stats.yml)中点击 **Run workflow** 手动触发。
- 每张卡片都标注最后成功取数时间，使用 UTC+8。
- GitHub 的任务排队、API 数据处理、Pages CDN 和 README 图片缓存都可能延迟展示，因此这是近实时统计，不保证秒级实时或严格 5 分钟内刷新。
- 如果取数或生成失败，之前成功发布的统计保持可见，更新时间不会伪装为最新。
- GitHub 可能在公共仓库连续 60 天没有活动时停用定时工作流；可在 Actions 页面重新启用。
- [完整统计页面](https://vighzhen.github.io/vighzhen/)会同时展示两张卡片和最后取数时间。

## 统计口径

| 指标 | 计算方式 |
| :--- | :--- |
| 全部仓库 | GitHub 用户 API 的公开仓库数，加上本人授权可访问的私有仓库数；分别标注公开、私有数量。 |
| 仓库星标 | 账号名下非 Fork 仓库的 `stargazerCount` 总和，包含私有仓库。不是账号给其他仓库点过的星标。 |
| 关注者 | GitHub 用户 API 的 `followers`。 |
| 今年贡献 | 当年 1 月 1 日至取数时刻，GitHub GraphQL 贡献日历的 `totalContributions`；使用本人访问凭证，包含公开及私有活动。 |
| 贡献明细 | 同一时间范围内 GitHub 的提交、PR、Issue 贡献计数。总贡献还包含其他类型活动，因此不要求明细之和等于总贡献。 |
| 代码语言 | 公开及私有的非 Fork 仓库语言字节数总和，排除主页仓库本身；展示前五种语言，其余合并为“其他”。 |

贡献统计遵循 GitHub 的归属规则，不等同于所有分支的提交数。私有仓库的贡献及代码语言纳入合计，不展示项目名称、链接或源码。语言比例不代表熟练程度。二次开发项目若没有 GitHub Fork 标记，按非 Fork 项目计入。

仓库数量使用 GitHub 用户索引；星标和语言统计仅汇总本人凭证实际可访问的仓库。如果 GitHub 用户 API 声明的私有仓库数量多于凭证可访问的数量，本次更新会失败，避免悄悄遗漏私有项目。

所有仓库分页完整读取；语言比例使用完整字节数作为分母，不重新放大前五种语言的比例。GitHub API 若返回不完整的语言数据，本次更新会失败而不会发布误导结果。

## 维护

生成脚本：`scripts/build_stats.py`  
工作流：`.github/workflows/profile-stats.yml`

Python 脚本只用标准库，通过 GitHub CLI 读取 API。取数步骤使用 GitHub 加密 Secret `PROFILE_STATS_TOKEN` 中的本人访问凭证。脚本会校验凭证所属账号，凭证不会写入源码、页面、图片或 JSON。其他发布步骤仍使用仓库自带的短期 `GITHUB_TOKEN`；结果直接部署到 Pages，不产生定时 Git 提交。

需要替换或撤销取数凭证时，可在仓库 **Settings → Secrets and variables → Actions** 中管理 `PROFILE_STATS_TOKEN`。替换凭证需要能读取全部要统计的私有仓库。工作流不响应 Pull Request，只在主分支修改、定时任务或手动触发时运行。

本地生成：

```bash
python3 scripts/build_stats.py --login vighzhen --output _site
```

结果包含四张 SVG、完整的 `stats.json` 和响应式统计页面。没有第三方统计卡片服务、外部字体或图片依赖。

本地生成同样需要以本人账号登录 GitHub CLI，并具有读取私有仓库的权限。只有汇总后的数据会写入输出目录。
