// 完整分段：保留所有字符，包括段落边界和表情符号。
export function splitText(text, limit = 3500) {
  const points = Array.from(String(text));
  const parts = [];
  while (points.length) {
    let length = 0, units = 0;
    while (length < points.length && units + points[length].length <= limit) units += points[length++].length;
    if (!length) throw new Error('分段长度过小');
    if (length < points.length) {
      const prefix = points.slice(0, length).join('');
      const paragraph = prefix.lastIndexOf('\n\n');
      if (paragraph > prefix.length / 2) length = Array.from(prefix.slice(0, paragraph + 2)).length;
    }
    parts.push(points.splice(0, length).join(''));
  }
  return parts;
}
export function numberedParts(text, limit = 3500) {
  const parts = splitText(text, limit);
  return parts.map((part, index) => parts.length === 1 ? part : `（${index + 1}/${parts.length}）\n${part}`);
}
export function deliveryText(result) {
  const body=result.status==='ready'?result.answer:result.message;
  const files=result.temporary_files||[];
  const cleanup=files.length?'待删除中间文件：\n'+files.map(x=>x.path+'（'+x.purpose+'）').join('\n'):'待删除中间文件：无';
  const debug=result.debug_dir?'\n调试文件：'+result.debug_dir:'';
  return String(body||'本机任务没有返回正文。')+'\n\n'+cleanup+debug;
}
