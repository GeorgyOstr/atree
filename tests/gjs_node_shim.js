// Run the Node-style extension tests under gjs when Node isn't installed:
//   gjs tests/gjs_node_shim.js "$PWD/tests/test_gnome_extension.js" ...
// Provides just the parts of node:test, node:assert/strict, node:fs, node:path and node:vm
// that the tests use.
const GLib = imports.gi.GLib;

const read = p => new TextDecoder().decode(GLib.file_get_contents(p)[1]);
const tests = [];

function deepEqual(a, b) {
    if (a === b)
        return true;
    if (typeof a !== 'object' || typeof b !== 'object' || !a || !b)
        return false;
    if (Array.isArray(a) !== Array.isArray(b))
        return false;
    const ka = Object.keys(a), kb = Object.keys(b);
    return ka.length === kb.length && ka.every(k => deepEqual(a[k], b[k]));
}

const show = v => {
    try {
        return JSON.stringify(v);
    } catch {
        return String(v);
    }
};
const fail = msg => {
    throw new Error(msg);
};

const assert = {
    equal: (a, b, m) => a === b || fail(`${m ?? ''} expected ${show(b)}, got ${show(a)}`),
    notEqual: (a, b, m) => a !== b || fail(`${m ?? ''} expected something other than ${show(b)}`),
    deepEqual: (a, b, m) => deepEqual(a, b) || fail(`${m ?? ''} expected ${show(b)}, got ${show(a)}`),
    ok: (v, m) => v || fail(m ?? `expected a truthy value, got ${show(v)}`),
    match: (s, re, m) => re.test(s) || fail(`${m ?? ''} ${show(s)} doesn't match ${re}`),
    doesNotThrow: (fn, m) => {
        try {
            fn();
        } catch (e) {
            fail(`${m ?? ''} threw ${e}`);
        }
    },
};

const path = {
    join: (...parts) => GLib.build_filenamev(parts),
    dirname: p => GLib.path_get_dirname(p),
    resolve: (...parts) => GLib.canonicalize_filename(GLib.build_filenamev(parts), null),
};

const fs = {
    readFileSync: (p, enc) => (enc ? read(p) : GLib.file_get_contents(p)[1]),
    existsSync: p => GLib.file_test(p, GLib.FileTest.EXISTS),
};

const vm = {
    createContext: sandbox => sandbox,
    runInContext: (code, sandbox) => {
        // eslint-disable-next-line no-new-func
        new Function('__sandbox', `with (__sandbox) {\n${code}\n}`)(sandbox);
        if (globalThis.__Ext) {
            sandbox.__Ext = globalThis.__Ext;
            delete globalThis.__Ext;
        }
    },
};

const builtins = {
    'node:test': (name, fn) => tests.push([name, fn]),
    'node:assert/strict': assert,
    'node:path': path,
    'node:fs': fs,
    'node:vm': vm,
};

const loaded = {};
function load(file) {
    if (loaded[file])
        return loaded[file].exports;
    const dir = GLib.path_get_dirname(file);
    const module = {exports: {}};
    loaded[file] = module;
    const require = name => (name in builtins
        ? builtins[name]
        : load(GLib.canonicalize_filename(name.endsWith('.js') ? name : `${name}.js`, dir)));
    // eslint-disable-next-line no-new-func
    new Function('require', 'module', 'exports', '__dirname', '__filename', read(file))(
        require, module, module.exports, dir, file);
    return module.exports;
}

let failed = 0;
for (const file of ARGV) {
    tests.length = 0;
    load(GLib.canonicalize_filename(file, GLib.get_current_dir()));
    for (const [name, fn] of tests) {
        try {
            fn();
            print(`ok   ${name}`);
        } catch (e) {
            failed++;
            print(`FAIL ${name}\n     ${e.message}`);
        }
    }
}
print(failed ? `${failed} failed` : 'all passed');
imports.system.exit(failed ? 1 : 0);
