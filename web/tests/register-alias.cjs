const Module = require("module");
const path = require("path");

const compiledSrcRoot = path.resolve(__dirname, "..", ".tmp-unit-tests", "src");
const originalResolveFilename = Module._resolveFilename;

Module._resolveFilename = function resolveWithAlias(request, parent, isMain, options) {
  if (typeof request === "string" && request.startsWith("@/")) {
    const mapped = path.join(compiledSrcRoot, request.slice(2));
    return originalResolveFilename.call(this, mapped, parent, isMain, options);
  }
  return originalResolveFilename.call(this, request, parent, isMain, options);
};
