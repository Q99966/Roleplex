/** 核对受控固件只替换 h1 正文，不能误改同名 title 或其他保留内容。
 * @param before 本轮第一轮生成的受控 HTML。
 * @param after 本轮实际文件内容。
 */
export function isExpectedHeadingEdit(before: string, after: string): boolean {
  const expected = before.replace(/(<h1\b[^>]*>\s*)HelloWorld(\s*<\/h1>)/i, '$1HelloWorld Updated$2')
  return expected !== before && after === expected
}
