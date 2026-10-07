/**
 * 公開デモで使えない操作に添える注記。
 *
 * **使えない機能を隠さない。** 隠すと、実装されていないのか公開デモの制限なのかが
 * 区別できず、できることまで少なく見える。入力欄は出したまま無効にして、理由を添える。
 */
export function DemoUnavailable({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-1 text-xs text-gray-500">
      <span className="mr-1 rounded bg-gray-100 px-1.5 py-0.5 font-medium text-gray-600">
        公開デモ
      </span>
      {children}
    </p>
  );
}
