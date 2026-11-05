"""Rebuild the A.4 performance tables/CSV from ALL measured results, including failures."""
import argparse,csv,io,json,statistics
from pathlib import Path


def span(values):return f'{min(values):.2f}–{max(values):.2f}' if values else '未测'
def median(values):return statistics.median(values)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True)
    p.add_argument('--initial',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    report=json.loads(a.input.read_text());initial=json.loads(a.initial.read_text());rows=report['runs'];out=a.output;out.mkdir(parents=True,exist_ok=True)
    assert len(rows)==report['summary']['runs'];assert sum(r['status']!='passed' for r in rows)==report['summary']['failed']
    light=[r for r in rows if r['spec']['variant']=='a4_fault_state' and not r['spec']['quarantined']]
    completed=sum(r['integrity_policy_at_write_end']['monitor_successes']>0 for r in light)
    def chosen(size,mode,clients,backup,variant):
        return [r for r in rows if not r['spec']['quarantined'] and
            (r['spec']['size_mib'],r['spec']['journal_mode'],r['spec']['clients'],r['spec']['backup'],r['spec']['variant'])==(size,mode,clients,backup,variant)]
    fields=['variant','size_mib','journal_mode','clients','backup','quarantined','seed','status','tps','committed','attempts',
        'p50_ms','p95_ms','p99_ms','lock_failure_fraction','cpu_user_seconds','cpu_system_seconds','peak_rss_kib',
        'logical_rchar','physical_write_bytes','monitor_attempts','monitor_completed','error_codes']
    stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=fields,lineterminator='\n');writer.writeheader()
    for r in rows:
        q=r.get('successful_latency',{});u=r.get('resources',{});i=u.get('io_delta',{});policy=r.get('integrity_policy_at_write_end',{})
        writer.writerow({**{k:r['spec'][k] for k in fields[:7]},'status':r['status'],'tps':r.get('tps'),'committed':r.get('committed'),'attempts':r.get('attempts'),
            'p50_ms':q.get('p50_ms'),'p95_ms':q.get('p95_ms'),'p99_ms':q.get('p99_ms'),'lock_failure_fraction':r.get('lock_failure_fraction'),
            'cpu_user_seconds':u.get('user_cpu_seconds'),'cpu_system_seconds':u.get('system_cpu_seconds'),'peak_rss_kib':u.get('peak_rss_kib'),
            'logical_rchar':i.get('rchar'),'physical_write_bytes':i.get('write_bytes'),'monitor_attempts':policy.get('monitor_attempts'),
            'monitor_completed':policy.get('monitor_successes'),'error_codes':json.dumps(r.get('errors_by_code',{}),sort_keys=True)})
    (out/'performance.csv').write_text(stream.getvalue())
    text=['# A4-03 最终实际性能结果','',
        '仅生成数据；热连接、FULL同步、2MiB/连接SQLite缓存；1/约79/160MiB，DELETE/WAL，1/4写者，两固定种子11/23。每例全新副本，版本按种子打乱，无同时运行本地其他重测试。A.4轻量显式启用1秒完整巡检及统一有界SQLite锁等待。启动登记/连接与全量检查单列，不冒充端到端首笔延迟。','',
        f"最终{len(rows)}案例：{report['summary']['passed']}通过、{report['summary']['failed']}失败；健康轻量案例{completed}/{len(light)}至少完成一次完整巡检。通过表示完整性/行数及允许锁失败断言成立，不等于所有业务请求成功；所有BUSY/闸门拒绝和检查超时次数均保留。",'',
        f"初版另有{initial['summary']['runs']}案例：{initial['summary']['passed']}通过、{initial['summary']['failed']}失败。两次固定A.3完整检查超时未删除；其只读取证工作副本完整性ok、独立行数与成功提交一致，不能归因结构损坏。初版零等待巡检有未完成现象，修复后另起最终全矩阵，未覆盖旧案例。",'',
        '## 约79MiB全部负载','',
        '每格为两轮实际TPS范围；加速比是两轮TPS中位数比。每轮p50/p95/p99、样本数、配置、CPU/RSS/I/O及版本见JSON/CSV，不合并百分位。原历史4.6–9.4 TPS的数据库布局/机器与此不同，不能声称完全复现同一实验。','',
        '|日志|写者|在线备份|A.2参考 TPS|A.3 TPS|A.4 full TPS|A.4轻量+巡检 TPS|轻量/A.3中位数比|',
        '|---|---:|---|---:|---:|---:|---:|---:|']
    variants=['a2','a3','a4_full','a4_fault_state']
    for mode in ['DELETE','WAL']:
        for clients in [1,4]:
            for backup in [False,True]:
                group={v:chosen(79,mode,clients,backup,v) for v in variants}
                ratio=median([r['tps'] for r in group['a4_fault_state']])/median([r['tps'] for r in group['a3']])
                text.append('|'+'|'.join([mode,str(clients),'是' if backup else '否']+
                    [span([r['tps'] for r in group[v]]) for v in variants]+[f'{ratio:.1f}x'])+'|')
    text+=['','## 容量趋势：单写者、无在线备份','',
        '|容量MiB|日志|A.3 TPS|A.4轻量+巡检 TPS|A.3每轮p99 ms范围|A.4每轮p99 ms范围|','|---:|---|---:|---:|---:|---:|']
    for size in [1,79,160]:
        for mode in ['DELETE','WAL']:
            old=chosen(size,mode,1,False,'a3');new=chosen(size,mode,1,False,'a4_fault_state')
            text.append('|'+ '|'.join([str(size),mode,span([r['tps'] for r in old]),span([r['tps'] for r in new]),
                span([r['successful_latency']['p99_ms'] for r in old]),span([r['successful_latency']['p99_ms'] for r in new])])+'|')
    text+=['','## CPU、内存、I/O：79MiB单写者，无在线备份','',
        '每格为两轮中位数，耗时范围另注。CPU包含同进程巡检/备份；峰值RSS包含初始化。/proc/self/io为进程归属，不是设备级IOPS。热OS缓存下read_bytes常为0，逻辑rchar仍反映完整扫描成本；不能据此说没有I/O。','',
        '|日志|版本|CPU ms/成功提交|峰值RSS MiB|逻辑读取MiB/成功提交|物理写入KiB/成功提交|完整检查ms范围|登记/连接启动s范围|',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    for mode in ['DELETE','WAL']:
        for variant in variants:
            group=chosen(79,mode,1,False,variant)
            values=[mode,variant,f"{median([(r['resources']['user_cpu_seconds']+r['resources']['system_cpu_seconds'])*1000/r['committed'] for r in group]):.3f}",
                f"{median([r['resources']['peak_rss_kib']/1024 for r in group]):.2f}",
                f"{median([r['resources']['io_delta']['rchar']/1048576/r['committed'] for r in group]):.3f}",
                f"{median([r['resources']['io_delta']['write_bytes']/1024/r['committed'] for r in group]):.3f}",
                span([r['initial_full_check']['ms'] for r in group]),span([r['startup_seconds'] for r in group])]
            text.append('|'+'|'.join(values)+'|')
    text+=['','## 锁等待、巡检与隔离','',
        '|版本|全部案例最大锁失败比例|隔离案例成功提交总数|','|---|---:|---:|']
    for variant in variants:
        group=[r for r in rows if r['spec']['variant']==variant]
        text.append(f"|{variant}|{100*max(r['lock_failure_fraction'] for r in group):.3f}%|{sum(r['committed'] for r in group if r['spec']['quarantined'])}|")
    text+=['', '隔离状态由生成故障意图注入，验证拒绝开销，不冒充原历史损坏库。每个健康轻量案例的巡检尝试/成功数都公开；完成1次不构成未来持续竞争下的严格发现时延保证。', '',
        '## 保证变化与解释','',
        '默认full仍每笔完整扫描，不能用轻量成绩声称默认性能已根本改变。fault_state显著提高本生成布局的大库稳态吞吐，但未发现损坏时仍可能继续提交；必须独立确认保证变化后显式选用。连接/巡检/正式研究/备份完整检查和已确认故障强阻断保留，A.2只作存在已知隔离缺口的性能参考，不推荐回退。','',
        '2秒/至少12个成功提交的短跑百分位只是描述值，不能承诺生产p99；所有案例实际样本数保留。并发仍受SQLite单写者与监控/备份读取影响，锁失败率非零时不能声称所有请求成功。各版本使用相同1秒锁/检查预算和FULL同步；不靠放宽预算、批量改变事务口径或丢失败制造收益。','',
        '后续可评估批量事件事务、异步有界队列、存储职责分拆或专用高并发存储，本轮不全面迁移数据库，也不自动在客户环境启用可选策略。原结构损坏产生根因仍root_cause_not_established。']
    (out/'PERFORMANCE.md').write_text('\n'.join(text)+'\n')
    print(json.dumps({'runs':len(rows),'failed':report['summary']['failed'],'light_monitor_completed_cases':completed,'light_cases':len(light)}))


if __name__=='__main__':main()
