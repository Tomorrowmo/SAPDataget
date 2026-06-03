// 简易表格组件 (虚拟滚动暂未启用)
export interface DataTableProps {
  rows: Record<string, unknown>[];
  maxRows?: number;
}

export default function DataTable({ rows, maxRows = 30 }: DataTableProps) {
  if (!rows.length) {
    return <div className="text-sm text-zinc-500">(无数据)</div>;
  }
  const cols = Object.keys(rows[0]);
  const display = rows.slice(0, maxRows);
  return (
    <div className="overflow-x-auto border border-zinc-200 rounded-lg bg-white">
      <table className="min-w-full text-sm">
        <thead className="bg-zinc-100 text-zinc-700">
          <tr>
            {cols.map((c) => (
              <th key={c} className="px-3 py-2 text-left font-medium whitespace-nowrap">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {display.map((row, i) => (
            <tr key={i} className="border-t border-zinc-100 hover:bg-zinc-50">
              {cols.map((c) => {
                const v = row[c];
                const isNum = typeof v === "number";
                return (
                  <td
                    key={c}
                    className={`px-3 py-2 whitespace-nowrap ${
                      isNum ? "text-right font-mono" : ""
                    }`}
                  >
                    {v === null || v === undefined
                      ? <span className="text-zinc-300">—</span>
                      : typeof v === "number"
                        ? v.toLocaleString("zh-CN")
                        : String(v)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > maxRows && (
        <div className="px-3 py-2 text-xs text-zinc-500 bg-zinc-50 border-t border-zinc-100">
          共 {rows.length} 行,仅显示前 {maxRows} 行,完整数据请下载 Excel。
        </div>
      )}
    </div>
  );
}
