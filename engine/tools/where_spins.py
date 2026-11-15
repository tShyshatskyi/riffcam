"""Which DLL is a busy thread running in? Samples a thread's instruction pointer (and the code addresses found on
its stack) N times and maps them to the process's modules. A poor man's profiler for when there's no debugger.

    python where_spins.py <pid> [samples]      (samples the thread with the most CPU time)
"""
import ctypes, ctypes.wintypes as wt, sys, time, collections, subprocess, json

k32, psapi = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("psapi", use_last_error=True)
H = ctypes.c_void_p
for f, args, res in ((k32.OpenProcess, [wt.DWORD, wt.BOOL, wt.DWORD], H), (k32.OpenThread, [wt.DWORD, wt.BOOL, wt.DWORD], H),
                     (k32.SuspendThread, [H], wt.DWORD), (k32.ResumeThread, [H], wt.DWORD),
                     (k32.GetThreadContext, [H, ctypes.c_void_p], wt.BOOL),
                     (k32.ReadProcessMemory, [H, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p], wt.BOOL),
                     (psapi.EnumProcessModulesEx, [H, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD], wt.BOOL),
                     (psapi.GetModuleBaseNameW, [H, H, wt.LPWSTR, wt.DWORD], wt.DWORD),
                     (psapi.GetModuleInformation, [H, H, ctypes.c_void_p, wt.DWORD], wt.BOOL)):
    f.argtypes, f.restype = args, res
PROCESS_ALL = 0x0410 | 0x0010  # QUERY_INFORMATION | VM_READ
THREAD_ALL = 0x0002 | 0x0008 | 0x0040  # SUSPEND_RESUME | GET_CONTEXT | QUERY_INFORMATION


class CONTEXT(ctypes.Structure):  # x64, only the parts we read; must be 16-byte aligned
    _fields_ = [("pad", ctypes.c_ubyte * 0x30), ("ContextFlags", wt.DWORD), ("rest", ctypes.c_ubyte * (0x4D0 - 0x34))]


def modules(pid):
    h = k32.OpenProcess(PROCESS_ALL, False, pid)
    arr = (ctypes.c_void_p * 1024)()
    need = wt.DWORD()
    psapi.EnumProcessModulesEx(h, arr, ctypes.sizeof(arr), ctypes.byref(need), 3)
    out = []
    for m in arr[: need.value // ctypes.sizeof(ctypes.c_void_p)]:
        name = ctypes.create_unicode_buffer(260)
        psapi.GetModuleBaseNameW(h, m, name, 260)
        info = (ctypes.c_void_p * 3)()
        psapi.GetModuleInformation(h, m, info, ctypes.sizeof(info))
        out.append((info[0] or 0, (info[0] or 0) + (info[1] or 0), name.value))
    return h, out


def where(mods, addr):
    for lo, hi, name in mods:
        if lo <= addr < hi:
            return f"{name}+0x{addr - lo:x}"
    return None


def busiest_thread(pid):
    ps = f"(Get-Process -Id {pid}).Threads | Sort-Object TotalProcessorTime -Descending | Select-Object -First 1 -ExpandProperty Id"
    return int(subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout)


def main():
    pid, n = int(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 40
    tid = busiest_thread(pid)
    hp, mods = modules(pid)
    th = k32.OpenThread(THREAD_ALL, False, tid)
    buf = ctypes.create_string_buffer(ctypes.sizeof(CONTEXT) + 16)
    ctx = CONTEXT.from_address((ctypes.addressof(buf) + 15) & ~15)
    rip_hits, stack_hits = collections.Counter(), collections.Counter()
    last_stack = []
    for _ in range(n):
        k32.SuspendThread(th)
        ctx.ContextFlags = 0x10001B  # CONTEXT_AMD64 | CONTROL | INTEGER | SEGMENTS... enough for Rip/Rsp
        ok = k32.GetThreadContext(th, ctypes.byref(ctx))
        rsp = int.from_bytes(bytes(ctypes.string_at(ctypes.addressof(ctx) + 0x98, 8)), "little")
        rip = int.from_bytes(bytes(ctypes.string_at(ctypes.addressof(ctx) + 0xF8, 8)), "little")
        stack = (ctypes.c_uint64 * 2048)()
        got = ctypes.c_size_t()
        k32.ReadProcessMemory(hp, ctypes.c_void_p(rsp), stack, ctypes.sizeof(stack), ctypes.byref(got))
        k32.ResumeThread(th)
        if not ok:
            continue
        rip_hits[where(mods, rip) or f"?0x{rip:x}"] += 1
        last_stack = list(stack[: got.value // 8])
        seen = set()
        for v in stack[: got.value // 8]:
            w = where(mods, v)
            if w:
                seen.add(w.split("+")[0])
        for m in seen:
            stack_hits[m] += 1
        time.sleep(0.05)
    print(f"thread {tid}, {n} samples\nwhere the CPU is (instruction pointer):")
    by_mod = collections.Counter()
    for k, v in rip_hits.items():
        by_mod[k.split("+")[0]] += v
    for k, v in by_mod.most_common(8):
        print(f"  {v:3d}  {k}")
    print("hottest addresses:")
    for k, v in rip_hits.most_common(6):
        print(f"  {v:3d}  {k}")
    print("modules with code addresses on the stack (share of samples):")
    for k, v in stack_hits.most_common(15):
        print(f"  {v:3d}  {k}")
    print("named functions on the stack, top first (needs a .pdb next to the exe; stack scan, so some are stale):")
    for name in symbolize(hp, mods, last_stack):
        print("  " + name)


def symbolize(hp, mods, stack, wanted=("RiffEngine.exe",)):
    dbg = ctypes.WinDLL("dbghelp")
    dbg.SymInitializeW.argtypes = [H, wt.LPCWSTR, wt.BOOL]
    dbg.SymFromAddrW.argtypes = [H, ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint64), ctypes.c_void_p]
    dbg.SymSetOptions(0x2 | 0x4)  # UNDNAME | DEFERRED_LOADS
    dbg.SymInitializeW(hp, None, True)
    buf = ctypes.create_string_buffer(88 + 512 * 2)
    ctypes.c_uint32.from_buffer(buf, 0).value = 88       # SizeOfStruct
    ctypes.c_uint32.from_buffer(buf, 80).value = 511     # MaxNameLen
    out, prev = [], None
    for v in stack:
        w = where(mods, v)
        if not w or w.split("+")[0] not in wanted:
            continue
        disp = ctypes.c_uint64()
        if dbg.SymFromAddrW(hp, v, ctypes.byref(disp), buf):
            name = ctypes.wstring_at(ctypes.addressof(buf) + 84)
            if name != prev:
                out.append(name)
            prev = name
    return out


if __name__ == "__main__":
    main()
