// D16: a planted HTML sink. `npx eslint --no-ignore lint-fixtures/planted.tsx` must fail (CI runs it).
export function Planted({ name }: { name: string }) {
  const el = document.createElement("div");
  el.innerHTML = name;
  return <div dangerouslySetInnerHTML={{ __html: name }} />;
}
