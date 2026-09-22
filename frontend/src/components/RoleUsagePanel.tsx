import { useEffect,useRef,useState } from 'react'
import { api,getAuthEpoch,type RoleExecutionUsage,type UsageMetric } from '../api/client'
import { useAppStore } from '../store/app'
import { useChatStore } from '../store/chat'

const labels = {cache_miss_tokens:'缓存未命中输入 Token',output_tokens:'输出 Token',cache_hit_tokens:'缓存命中 Token',input_cache_hit_ratio:'输入缓存命中率'} as const
const stopReasons:Record<string,string>={decision_budget:'决策预算停止',graph_budget:'图保护停止',user_cancelled:'用户停止',provider_failed:'模型请求失败',protocol_error:'执行异常',interrupted:'执行中断',context_rejected:'上下文检查未通过'}
const statuses:Record<string,string>={queued:'排队中',running:'执行中',completed:'正常结束',stopped:'已停止',failed:'失败'}

/** 显示完整值或明确标注已记录部分，不将未知显示成零。
 * @param metric 服务端汇总值。
 * @param time 是否将毫秒格式化为秒。
 * @param ratio 是否将比例格式化为百分比。
 */
function MetricValue({metric,time=false,ratio=false}:{metric:UsageMetric;time?:boolean;ratio?:boolean}) {
  const format=(value:number)=>ratio?`${(value*100).toFixed(1)}%`:time?`${(value/1000).toFixed(2)} 秒`:value.toLocaleString()
  return <><p className="break-all font-mono text-sm text-slate-100">{metric.total===null?'未知':format(metric.total)}</p>
    {metric.total===null&&metric.known!==null&&<p className="mt-1 text-[10px] text-slate-500">{ratio?'已记录部分':'已记录'} {format(metric.known)}</p>}</>
}

/** 当前会话的角色用量，展开后才查询；身份变化丢弃迟到响应。
 * @param conversationId 当前会话身份。
 * @param roleId 当前角色身份。
 */
export function RoleUsagePanel({conversationId,roleId}:{conversationId:number;roleId:number}) {
  const user=useAppStore(state=>state.user)
  const world=useAppStore(state=>state.worldName)
  const active=useChatStore(state=>state.conversationId===conversationId&&state.activeGenerationIds.length>0)
  const messageKey=useChatStore(state=>{
    if(state.conversationId!==conversationId)return ''
    const latest=state.messages.filter(message=>message.sender_type==='role'&&message.sender_id===roleId).at(-1)
    return latest?`${latest.id}:${latest.status}`:''
  })
  const [open,setOpen]=useState(false)
  const [mode,setMode]=useState<'latest'|'cumulative'>('latest')
  const [data,setData]=useState<{scope:string;value:RoleExecutionUsage}|null>(null)
  const [error,setError]=useState('')
  const [reload,setReload]=useState(0)
  const sequence=useRef(0)
  const scope=`${world}:${user?.id}:${conversationId}:${roleId}:${getAuthEpoch()}`
  useEffect(()=>{
    if(!open||!user?.is_owner)return
    const id=++sequence.current,controller=new AbortController()
    let pending=false
    setError('')
    /** 按调用级数据刷新，不随每个流式 Token 发请求。 */
    async function load(){
      if(pending)return
      pending=true
      try{
        const value=await api.roleExecutionUsage(conversationId,roleId,controller.signal)
        if(!controller.signal.aborted&&id===sequence.current){setData({scope,value});setError('')}
      }catch{
        if(!controller.signal.aborted&&id===sequence.current)setError('用量暂时无法加载，请重试。')
      }finally{pending=false}
    }
    void load()
    const timer=active?window.setInterval(()=>void load(),2000):undefined
    return()=>{controller.abort();if(timer!==undefined)window.clearInterval(timer);sequence.current++}
  },[open,user?.is_owner,scope,conversationId,roleId,active,messageKey,reload])
  if(!user?.is_owner)return null
  const value=data?.scope===scope?data.value:null
  const summary=mode==='latest'?value?.latest?.summary:value?.cumulative
  return <details className="mt-3 border-t border-slate-800 pt-2 text-xs" onToggle={event=>setOpen(event.currentTarget.open)}>
    <summary className="cursor-pointer text-indigo-500">执行用量</summary>
    {open&&<section aria-label="角色执行用量" className="mt-3 space-y-3">
      <div className="flex rounded-lg bg-slate-900 p-1" role="group" aria-label="用量范围">
        {(['latest','cumulative'] as const).map(item=><button type="button" key={item} aria-pressed={mode===item} onClick={()=>setMode(item)}
          className={`flex-1 rounded-md px-2 py-1.5 ${mode===item?'bg-panel text-indigo-500 shadow-sm':'text-slate-500'}`}>{item==='latest'?'最近一次':'本会话累计'}</button>)}
      </div>
      {error&&<p role="alert" className="text-red-300">{error} <button type="button" onClick={()=>setReload(value=>value+1)} className="underline">重试用量查询</button></p>}
      {!value&&!error&&<p className="text-slate-500">正在加载用量…</p>}
      {value&&mode==='latest'&&!value.latest&&<p className="text-slate-500">还没有执行记录。</p>}
      {summary&&<>
        {mode==='latest'&&value?.latest&&<div className="flex flex-wrap justify-between gap-2 text-slate-400">
          <span>{stopReasons[value.latest.stop_reason??'']??statuses[value.latest.status]??value.latest.status}</span>
          <span>整轮耗时 {value.latest.duration_ms===null?'未知':`${(value.latest.duration_ms/1000).toFixed(2)} 秒`}</span>
        </div>}
        {mode==='latest'&&value?.latest?.model_name&&<p className="break-all text-[10px] text-slate-500">执行模型：{value.latest.model_name}</p>}
        {mode==='latest'&&value?.latest?.execution_kind==='context_compact'&&<p className="text-slate-500">本次为上下文压缩维护，调用计入该角色用量。</p>}
        {mode==='latest'&&value?.latest?.error_code&&<p className="break-all text-red-300">{value.latest.error_code}</p>}
        <p className="text-slate-500">{mode==='cumulative'?`${summary.executions} 次执行 · `:''}已记录模型调用 {summary.recorded_calls} 次</p>
        <div className="grid grid-cols-2 gap-2">
          {Object.entries(labels).map(([key,label])=><div key={key} role="group" aria-label={label} className="rounded-lg bg-slate-900 p-2">
            <p className="mb-1 text-[10px] text-slate-500">{label}</p><MetricValue metric={summary.metrics[key as keyof typeof labels]} ratio={key==='input_cache_hit_ratio'} />
          </div>)}
        </div>
        <div className="flex items-start justify-between gap-2 text-slate-500"><span>模型调用耗时合计</span><MetricValue metric={summary.metrics.model_duration_ms} time /></div>
        {summary.untracked_messages>0&&<p className="text-amber-300">另有 {summary.untracked_messages} 条旧角色回复没有执行记录，无法完整累计。</p>}
        {summary.untracked_executions>0&&<p className="text-amber-300">包含 {summary.untracked_executions} 次旧执行未采集，无法完整累计。</p>}
        {summary.missing_call_records>0&&<p className="text-amber-300">有 {summary.missing_call_records} 次决策缺少调用记录，统计不完整。</p>}
        <p className="text-[10px] leading-relaxed text-slate-500">仅使用厂商已报告的 Token，缺失显示未知。未命中量与命中率仅按输入和缓存命中数据均完整的调用计算；命中率为命中量除以输入总量。SDK 内部重试未逐次计量，此处不是费用账单。</p>
      </>}
    </section>}
  </details>
}
