import type { ToolDetails } from '../api/client'

const reasons: Record<string,string> = {
  missing:'缺少必填字段', unexpected_field:'不允许的额外字段', invalid_combination:'参数组合不符合工具定义',
  wrong_type:'字段类型不正确', invalid_choice:'值不在允许选项中', out_of_range:'超出允许范围',
  invalid_value:'字段值不符合要求', invalid_json:'参数不是合法 JSON',
}

/** 显示可修正的参数问题，路径来自服务端白名单，不展示原始值。
 * @param value 已授权 Owner 的有界参数诊断。
 */
export function ArgumentErrorNote({ value }: { value: NonNullable<ToolDetails['argument_error']> }) {
  return <div className="space-y-1 text-amber-300">
    <p>{value.error_code === 'TOOL_NOT_AVAILABLE' ? '请选择本轮已提供的工具。' : '可按工具定义修正参数后继续；本次未执行。'}</p>
    {value.issues.map((issue,index) => <p key={index}>
      <span className="font-mono">{issue.path.length ? issue.path.map(part => typeof part === 'number' ? `[${part}]` : part).join('.') : '参数整体'}</span>
      {'：'}{reasons[issue.reason] ?? '参数校验未通过'}
    </p>)}
  </div>
}
