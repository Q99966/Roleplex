import type { EditError } from '../api/client'

/** 显示原输入中的失败片段序号，不把校验失败误读为部分文件提交。
 * @param value 经过服务端验证的编辑失败信息。
 */
export function EditErrorNote({ value }: { value: EditError }) {
  return <p className="text-amber-300">第 {value.replacement_index} 处替换未通过校验。
    {value.conflicting_replacement_index ? `与第 ${value.conflicting_replacement_index} 处替换重叠。` : ''}
    {value.recovery === 'split_non_overlapping' ? '请拆分为不重叠的片段后重试。' : '请重新读取文件并调整匹配片段后重试。'}
  </p>
}
