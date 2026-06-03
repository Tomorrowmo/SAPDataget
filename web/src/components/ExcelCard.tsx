// Excel 下载卡片
interface ExcelCardProps {
  filename: string;
  size_bytes: number;
  download_url: string;
  row_count?: number;
}

export default function ExcelCard({ filename, size_bytes, download_url, row_count }: ExcelCardProps) {
  const kb = (size_bytes / 1024).toFixed(1);
  return (
    <a
      href={download_url}
      className="inline-flex items-center gap-3 px-4 py-3 rounded-lg border border-emerald-200 bg-emerald-50 hover:bg-emerald-100 transition no-underline"
      download={filename}
    >
      <div className="w-10 h-10 rounded bg-emerald-600 text-white flex items-center justify-center font-bold">
        XLS
      </div>
      <div className="flex-1 min-w-0">
        <div className="font-medium text-emerald-900 truncate" style={{ maxWidth: 360 }}>{filename}</div>
        <div className="text-xs text-emerald-700">
          {row_count !== undefined ? `${row_count} 行 · ` : ""}{kb} KB · 点击下载
        </div>
      </div>
      <div className="text-emerald-600">⬇</div>
    </a>
  );
}
