function splitMethodFullName(fullName: string): { className: string; method: string } {
  const [pathPart] = fullName.split(":");
  const segments = pathPart.split(".");
  const method = segments.pop() ?? pathPart;
  return { className: segments.join("."), method };
}

function shortClassNameFromClass(classFullName: string): string {
  const segments = classFullName.split(".");
  return segments.pop() ?? classFullName;
}

export function shortClassName(methodFullName: string): string {
  return shortClassNameFromClass(splitMethodFullName(methodFullName).className);
}

export function shortLabel(methodFullName: string): string {
  const { className, method } = splitMethodFullName(methodFullName);
  const classNameShort = shortClassNameFromClass(className);
  return method === "<init>" ? `new ${classNameShort}()` : `${classNameShort}.${method}()`;
}
