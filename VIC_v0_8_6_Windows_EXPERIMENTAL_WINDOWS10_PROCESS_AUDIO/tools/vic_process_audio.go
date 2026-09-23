//go:build windows

package main

import (
    "flag"
    "fmt"
    "os"
    "runtime"
    "sync/atomic"
    "syscall"
    "time"
    "unsafe"
)

type GUID struct {
    Data1 uint32
    Data2 uint16
    Data3 uint16
    Data4 [8]byte
}

var (
    iidIUnknown = GUID{0x00000000, 0x0000, 0x0000, [8]byte{0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46}}
    iidIAudioClient = GUID{0x1CB9AD4C, 0xDBFA, 0x4C32, [8]byte{0xB1, 0x78, 0xC2, 0xF5, 0x68, 0xA7, 0x03, 0xB2}}
    iidIAudioCaptureClient = GUID{0xC8ADBD64, 0xE71E, 0x48A0, [8]byte{0xA4, 0xDE, 0x18, 0x5C, 0x39, 0x5C, 0xD3, 0x17}}
    iidIActivateCompletion = GUID{0x41D949AB, 0x9862, 0x444A, [8]byte{0x80, 0xF6, 0xC2, 0x61, 0x33, 0x4D, 0xA5, 0xEB}}
    iidIAgileObject = GUID{0x94EA2B94, 0xE9CC, 0x49E0, [8]byte{0xC0, 0xFF, 0xEE, 0x64, 0xCA, 0x8F, 0x5B, 0x90}}
)

const (
    sOK uintptr = 0
    eNoInterface uintptr = 0x80004002
    coinitMultithreaded = 0x0
    vtBlob = 65
    audioClientActivationTypeProcessLoopback = 1
    processLoopbackIncludeTargetProcessTree = 0
    audclntSharemodeShared = 0
    audclntStreamflagsLoopback = 0x00020000
    audclntStreamflagsEventCallback = 0x00040000
    audclntStreamflagsSrcDefaultQuality = 0x08000000
    audclntStreamflagsAutoConvertPCM = 0x80000000
    audclntBufferflagsSilent = 0x2
    waveFormatPCM = 1
    waitObject0 = 0
    waitTimeout = 258
    infinite = 0xFFFFFFFF
)

type blob struct {
    Size uint32
    _ uint32
    Data uintptr
}

type propVariant struct {
    VT uint16
    Reserved1 uint16
    Reserved2 uint16
    Reserved3 uint16
    Blob blob
}

type activationParams struct {
    ActivationType uint32
    TargetProcessID uint32
    ProcessLoopbackMode uint32
}

type waveFormatEx struct {
    FormatTag uint16
    Channels uint16
    SamplesPerSec uint32
    AvgBytesPerSec uint32
    BlockAlign uint16
    BitsPerSample uint16
    ExtraSize uint16
}

type completionVtbl struct {
    QueryInterface uintptr
    AddRef uintptr
    Release uintptr
    ActivateCompleted uintptr
}

type completionHandler struct {
    Vtbl *completionVtbl
    Refs uint32
    Event syscall.Handle
    Result int32
    AudioClient uintptr
}

var (
    ole32 = syscall.NewLazyDLL("ole32.dll")
    mmdevapi = syscall.NewLazyDLL("mmdevapi.dll")
    kernel32 = syscall.NewLazyDLL("kernel32.dll")
    procCoInitializeEx = ole32.NewProc("CoInitializeEx")
    procCoUninitialize = ole32.NewProc("CoUninitialize")
    procActivateAudioInterfaceAsync = mmdevapi.NewProc("ActivateAudioInterfaceAsync")
    procCreateEventW = kernel32.NewProc("CreateEventW")
    procSetEvent = kernel32.NewProc("SetEvent")
    procWaitForSingleObject = kernel32.NewProc("WaitForSingleObject")
    procCloseHandle = kernel32.NewProc("CloseHandle")
)

var handlerVtbl = completionVtbl{
    QueryInterface: syscall.NewCallback(handlerQueryInterface),
    AddRef: syscall.NewCallback(handlerAddRef),
    Release: syscall.NewCallback(handlerRelease),
    ActivateCompleted: syscall.NewCallback(handlerActivateCompleted),
}

func guidEqual(a, b *GUID) bool { return a != nil && b != nil && *a == *b }

func handlerQueryInterface(this, riid, ppv uintptr) uintptr {
    if ppv == 0 || riid == 0 { return 0x80004003 }
    requested := (*GUID)(unsafe.Pointer(riid))
    if guidEqual(requested, &iidIUnknown) || guidEqual(requested, &iidIActivateCompletion) || guidEqual(requested, &iidIAgileObject) {
        *(*uintptr)(unsafe.Pointer(ppv)) = this
        handlerAddRef(this)
        return sOK
    }
    *(*uintptr)(unsafe.Pointer(ppv)) = 0
    return eNoInterface
}
func handlerAddRef(this uintptr) uintptr {
    h := (*completionHandler)(unsafe.Pointer(this))
    return uintptr(atomic.AddUint32(&h.Refs, 1))
}
func handlerRelease(this uintptr) uintptr {
    h := (*completionHandler)(unsafe.Pointer(this))
    return uintptr(atomic.AddUint32(&h.Refs, ^uint32(0)))
}
func handlerActivateCompleted(this, operation uintptr) uintptr {
    h := (*completionHandler)(unsafe.Pointer(this))
    if operation == 0 {
        h.Result = -2147467259
        procSetEvent.Call(uintptr(h.Event))
        return sOK
    }
    vtbl := *(*uintptr)(unsafe.Pointer(operation))
    getActivateResult := *(*uintptr)(unsafe.Pointer(vtbl + 3*unsafe.Sizeof(uintptr(0))))
    var result int32
    var activated uintptr
    hr, _, _ := syscall.SyscallN(getActivateResult, operation, uintptr(unsafe.Pointer(&result)), uintptr(unsafe.Pointer(&activated)))
    if int32(hr) < 0 {
        h.Result = int32(hr)
    } else {
        h.Result = result
        h.AudioClient = activated
    }
    procSetEvent.Call(uintptr(h.Event))
    return sOK
}

func comMethod(object uintptr, index uintptr, args ...uintptr) uintptr {
    vtbl := *(*uintptr)(unsafe.Pointer(object))
    method := *(*uintptr)(unsafe.Pointer(vtbl + index*unsafe.Sizeof(uintptr(0))))
    callArgs := make([]uintptr, 0, len(args)+1)
    callArgs = append(callArgs, object)
    callArgs = append(callArgs, args...)
    result, _, _ := syscall.SyscallN(method, callArgs...)
    return result
}

func release(object uintptr) {
    if object != 0 { comMethod(object, 2) }
}

func hresultError(label string, hr uintptr) error {
    return fmt.Errorf("%s failed: HRESULT 0x%08X", label, uint32(hr))
}

func main() {
    runtime.LockOSThread()
    defer runtime.UnlockOSThread()

    pid := flag.Uint("pid", 0, "target process ID")
    rate := flag.Uint("rate", 44100, "sample rate")
    channels := flag.Uint("channels", 2, "channel count")
    flag.Parse()
    if *pid == 0 {
        fmt.Fprintln(os.Stderr, "VIC_APP_AUDIO_ERROR target PID is required")
        os.Exit(2)
    }
    if *channels < 1 || *channels > 2 {
        fmt.Fprintln(os.Stderr, "VIC_APP_AUDIO_ERROR channels must be 1 or 2")
        os.Exit(2)
    }

    hr, _, _ := procCoInitializeEx.Call(0, coinitMultithreaded)
    if int32(hr) < 0 && uint32(hr) != 0x80010106 {
        fmt.Fprintln(os.Stderr, hresultError("CoInitializeEx", hr))
        os.Exit(3)
    }
    if int32(hr) >= 0 { defer procCoUninitialize.Call() }

    event, _, _ := procCreateEventW.Call(0, 0, 0, 0)
    if event == 0 {
        fmt.Fprintln(os.Stderr, "VIC_APP_AUDIO_ERROR CreateEvent failed")
        os.Exit(3)
    }
    defer procCloseHandle.Call(event)

    handler := &completionHandler{Vtbl: &handlerVtbl, Refs: 1, Event: syscall.Handle(event)}
    params := activationParams{
        ActivationType: audioClientActivationTypeProcessLoopback,
        TargetProcessID: uint32(*pid),
        ProcessLoopbackMode: processLoopbackIncludeTargetProcessTree,
    }
    variant := propVariant{VT: vtBlob, Blob: blob{Size: uint32(unsafe.Sizeof(params)), Data: uintptr(unsafe.Pointer(&params))}}
    path, _ := syscall.UTF16PtrFromString("VAD\\Process_Loopback")
    var operation uintptr
    hr, _, _ = procActivateAudioInterfaceAsync.Call(
        uintptr(unsafe.Pointer(path)),
        uintptr(unsafe.Pointer(&iidIAudioClient)),
        uintptr(unsafe.Pointer(&variant)),
        uintptr(unsafe.Pointer(handler)),
        uintptr(unsafe.Pointer(&operation)),
    )
    if int32(hr) < 0 {
        fmt.Fprintln(os.Stderr, hresultError("ActivateAudioInterfaceAsync", hr))
        os.Exit(4)
    }
    defer release(operation)

    wait, _, _ := procWaitForSingleObject.Call(event, 15000)
    if wait != waitObject0 {
        fmt.Fprintf(os.Stderr, "VIC_APP_AUDIO_ERROR activation timed out (%d)\n", wait)
        os.Exit(4)
    }
    if handler.Result < 0 || handler.AudioClient == 0 {
        fmt.Fprintf(os.Stderr, "VIC_APP_AUDIO_ERROR activation result 0x%08X\n", uint32(handler.Result))
        os.Exit(4)
    }
    client := handler.AudioClient
    defer release(client)

    bits := uint16(16)
    blockAlign := uint16(*channels) * bits / 8
    format := waveFormatEx{
        FormatTag: waveFormatPCM,
        Channels: uint16(*channels),
        SamplesPerSec: uint32(*rate),
        AvgBytesPerSec: uint32(*rate) * uint32(blockAlign),
        BlockAlign: blockAlign,
        BitsPerSample: bits,
        ExtraSize: 0,
    }
    flags := uintptr(audclntStreamflagsLoopback | audclntStreamflagsEventCallback | audclntStreamflagsSrcDefaultQuality | audclntStreamflagsAutoConvertPCM)
    hr = comMethod(client, 3,
        audclntSharemodeShared,
        flags,
        uintptr(int64(2_000_000)),
        0,
        uintptr(unsafe.Pointer(&format)),
        0,
    )
    if int32(hr) < 0 {
        fmt.Fprintln(os.Stderr, hresultError("IAudioClient.Initialize", hr))
        os.Exit(5)
    }

    sampleEvent, _, _ := procCreateEventW.Call(0, 0, 0, 0)
    if sampleEvent == 0 {
        fmt.Fprintln(os.Stderr, "VIC_APP_AUDIO_ERROR sample event creation failed")
        os.Exit(5)
    }
    defer procCloseHandle.Call(sampleEvent)
    hr = comMethod(client, 13, sampleEvent)
    if int32(hr) < 0 {
        fmt.Fprintln(os.Stderr, hresultError("IAudioClient.SetEventHandle", hr))
        os.Exit(5)
    }

    var capture uintptr
    hr = comMethod(client, 14, uintptr(unsafe.Pointer(&iidIAudioCaptureClient)), uintptr(unsafe.Pointer(&capture)))
    if int32(hr) < 0 || capture == 0 {
        fmt.Fprintln(os.Stderr, hresultError("IAudioClient.GetService", hr))
        os.Exit(5)
    }
    defer release(capture)

    hr = comMethod(client, 10)
    if int32(hr) < 0 {
        fmt.Fprintln(os.Stderr, hresultError("IAudioClient.Start", hr))
        os.Exit(5)
    }
    defer comMethod(client, 11)

    fmt.Fprintf(os.Stderr, "VIC_APP_AUDIO_READY pid=%d rate=%d channels=%d\n", *pid, *rate, *channels)
    output := os.Stdout
    zeroes := make([]byte, 1024*1024)

    for {
        wait, _, _ = procWaitForSingleObject.Call(sampleEvent, 1000)
        if wait != waitObject0 && wait != waitTimeout {
            fmt.Fprintf(os.Stderr, "VIC_APP_AUDIO_ERROR wait failed %d\n", wait)
            os.Exit(6)
        }
        for {
            var packetFrames uint32
            hr = comMethod(capture, 5, uintptr(unsafe.Pointer(&packetFrames)))
            if int32(hr) < 0 {
                fmt.Fprintln(os.Stderr, hresultError("IAudioCaptureClient.GetNextPacketSize", hr))
                os.Exit(6)
            }
            if packetFrames == 0 { break }

            var data uintptr
            var frames uint32
            var bufferFlags uint32
            var devicePosition uint64
            var qpcPosition uint64
            hr = comMethod(capture, 3,
                uintptr(unsafe.Pointer(&data)),
                uintptr(unsafe.Pointer(&frames)),
                uintptr(unsafe.Pointer(&bufferFlags)),
                uintptr(unsafe.Pointer(&devicePosition)),
                uintptr(unsafe.Pointer(&qpcPosition)),
            )
            if int32(hr) < 0 {
                fmt.Fprintln(os.Stderr, hresultError("IAudioCaptureClient.GetBuffer", hr))
                os.Exit(6)
            }
            byteCount := int(frames) * int(blockAlign)
            var writeErr error
            if bufferFlags&audclntBufferflagsSilent != 0 || data == 0 {
                remaining := byteCount
                for remaining > 0 {
                    chunk := remaining
                    if chunk > len(zeroes) { chunk = len(zeroes) }
                    _, writeErr = output.Write(zeroes[:chunk])
                    if writeErr != nil { break }
                    remaining -= chunk
                }
            } else if byteCount > 0 {
                bytes := unsafe.Slice((*byte)(unsafe.Pointer(data)), byteCount)
                _, writeErr = output.Write(bytes)
            }
            releaseHR := comMethod(capture, 4, uintptr(frames))
            if int32(releaseHR) < 0 {
                fmt.Fprintln(os.Stderr, hresultError("IAudioCaptureClient.ReleaseBuffer", releaseHR))
                os.Exit(6)
            }
            if writeErr != nil {
                // Broken pipe means the VIC worker has stopped reading.
                time.Sleep(20 * time.Millisecond)
                return
            }
        }
    }
}
