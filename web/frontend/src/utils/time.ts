/** 消息时间戳格式化工具。 */

const timeFmt = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
const monthDayFmt = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric' });
const fullDateFmt = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' });

/** 格式化消息时间：今天 "HH:mm"，今年 "M月D日 HH:mm"，跨年 "YYYY年M月D日 HH:mm"。 */
export function formatMessageTime(ts: number | null | undefined): string {
  if (!ts) return '';
  const d = new Date(ts);
  const now = new Date();
  const time = timeFmt.format(d);
  if (isSameDay(d, now)) return time;
  if (d.getFullYear() === now.getFullYear()) return `${monthDayFmt.format(d)} ${time}`;
  return `${fullDateFmt.format(d)} ${time}`;
}

/** 两个时间戳是否不在同一天。 */
export function isDifferentDay(a: number | null | undefined, b: number | null | undefined): boolean {
  if (!a || !b) return false;
  return !isSameDay(new Date(a), new Date(b));
}

/** 日期分隔标签："今天"、"昨天"、"M月D日"、"YYYY年M月D日"。 */
export function formatDateSeparator(ts: number | null | undefined): string {
  if (!ts) return '';
  const d = new Date(ts);
  const now = new Date();
  if (isSameDay(d, now)) return '今天';
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (isSameDay(d, yesterday)) return '昨天';
  if (d.getFullYear() === now.getFullYear()) return monthDayFmt.format(d);
  return fullDateFmt.format(d);
}

function isSameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}
