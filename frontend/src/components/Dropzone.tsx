import { FileText, UploadCloud } from "lucide-react";
import { useRef, useState } from "react";
import { cx } from "./ui";

export const ACCEPTED = [".pdf", ".docx", ".txt", ".md"];
export const MAX_MB = 20;

export function validateFile(file: File): string | null {
  const ext = "." + (file.name.split(".").pop() || "").toLowerCase();
  if (!ACCEPTED.includes(ext)) return "Upload a PDF, Word (.docx) or plain-text file.";
  if (file.size === 0) return "The file is empty.";
  if (file.size > MAX_MB * 1024 * 1024) return `The file is larger than ${MAX_MB} MB.`;
  return null;
}

export function Dropzone({ onFile, file, disabled, compact }: { onFile: (f: File) => void; file?: File | null; disabled?: boolean; compact?: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accept = (f: File | undefined) => {
    if (!f) return;
    const err = validateFile(f);
    setError(err);
    if (!err) onFile(f);
  };

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        aria-disabled={disabled}
        aria-label="Upload a document: drop a file here or press Enter to choose one"
        onClick={() => !disabled && input.current?.click()}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && !disabled) {
            e.preventDefault();
            input.current?.click();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          if (!disabled) accept(e.dataTransfer.files[0]);
        }}
        className={cx(
          "group relative flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed text-center transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500",
          compact ? "px-6 py-8" : "px-6 py-14 sm:py-16",
          over
            ? "border-brand-500 bg-brand-50/70 dark:bg-brand-500/10"
            : "border-slate-300 bg-slate-50/60 hover:border-brand-400 hover:bg-brand-50/40 dark:border-slate-700 dark:bg-slate-900/40 dark:hover:border-brand-500/60 dark:hover:bg-brand-500/5",
          disabled && "cursor-not-allowed opacity-60",
        )}
      >
        <input
          ref={input}
          type="file"
          accept={ACCEPTED.join(",")}
          className="sr-only"
          tabIndex={-1}
          onChange={(e) => {
            accept(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
        {file ? (
          <>
            <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-100 text-brand-700 dark:bg-brand-500/15 dark:text-brand-300">
              <FileText className="h-6 w-6" aria-hidden />
            </div>
            <p className="max-w-full truncate font-semibold">{file.name}</p>
            <p className="mt-1 text-sm text-slate-500">{(file.size / 1024).toFixed(0)} KB · click to choose a different file</p>
          </>
        ) : (
          <>
            <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-white text-brand-600 shadow-card ring-1 ring-slate-200 transition group-hover:scale-105 dark:bg-slate-800 dark:text-brand-300 dark:ring-slate-700">
              <UploadCloud className="h-7 w-7" aria-hidden />
            </div>
            <p className="text-base font-semibold">
              Drop your document here, or <span className="text-brand-600 dark:text-brand-400">browse</span>
            </p>
            <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">PDF, Word (.docx) or text · up to {MAX_MB} MB</p>
          </>
        )}
      </div>
      {error && (
        <p role="alert" className="mt-2 text-sm font-medium text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
    </div>
  );
}
