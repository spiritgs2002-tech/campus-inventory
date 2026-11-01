# P8a遗留问题处理报告

日期：2026-11-01。依据：[P8a整改验收报告](P8a整改验收报告.md)。原验收报告保持原文。

| 遗留项 | 实际处理 | 验证结果 |
|---|---|---|
| 三份schema关系在仓库主文档说明 | 新增根README.md，列明唯一当前源、自动打包快照、冻结历史基线及同步方式 | 文件路径及同步命令已核对；未改动SQL或迁移 |
| 两个“可删”隔离区处理 | 改名为_codex_raw_logs(备查)和_django_codex_logs(备查)，保留原始材料；加入.gitignore排除提交 | git check-ignore通过；未删除审计材料 |
| Redis/Celery/就绪探针联通缺少本轮验证 | 使用临时PostgreSQL16.15、真实本机Redis7.2.17及独立Celery worker，重新执行verify_p6.py、verify_p7.py、verify_readiness.py | 三个脚本全部成功退出；临时数据库、LOGIN、Redis、worker及对象服务已清理 |

本轮另外对当前完整schema执行独立建库验证：G0=27表/14函数/32触发器，21项SQL正反用例通过。git diff --check通过。本次未重跑200项全量测试，不将上轮结果记作新测。

本地原始日志位于 `docs/过程记录/P8a/_codex_raw_logs(备查)/遗留复验/`：P6真实联通日志.txt、P7真实联通日志.txt、就绪探针真实联通日志.txt、当前完整SQL实库验证.json、临时集群生命周期日志.txt。该目录由Git忽略，保留供本机复核。

对象存储使用本地S3协议测试服务，未连接正式云桶；真实目标部署环境与远端CI不在本轮验证范围。三项遗留处理完毕，未提交或推送代码。
