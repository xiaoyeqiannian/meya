using System.Buffers.Binary;
using System.Text;
using System.Text.Json;
using Meya.Core;
using Meya.Windows;
using NAudio.Wave;

Console.OutputEncoding = new UTF8Encoding(false);

string root = args.Length > 0 ? Path.GetFullPath(args[0]) : FindRoot(AppContext.BaseDirectory);
VerifyFrameFixtures(Path.Combine(root, "contracts", "ipc-v2-fixtures.json"));
VerifyFragmentationAndResynchronization();
VerifyStreamingPcmConversion();
VerifySessionTraces(Path.Combine(root, "contracts", "session-traces.json"));
string? smokeAudio = args.Length > 1 && File.Exists(args[1])
    ? args[1]
    : Environment.GetEnvironmentVariable("MEYA_WORKER_SMOKE_WAV");
int smokeIndex = Array.IndexOf(args, "--worker-smoke");
if (string.IsNullOrWhiteSpace(smokeAudio) && smokeIndex >= 0)
{
    if (smokeIndex + 1 >= args.Length)
    {
        throw new ArgumentException("--worker-smoke requires a WAV path");
    }
    smokeAudio = args[smokeIndex + 1];
}
if (!string.IsNullOrWhiteSpace(smokeAudio))
{
    await VerifyWorkerAsync(root, smokeAudio);
}
int previewSmokeIndex = Array.IndexOf(args, "--preview-smoke");
if (previewSmokeIndex >= 0)
{
    if (previewSmokeIndex + 1 >= args.Length)
    {
        throw new ArgumentException("--preview-smoke requires a WAV path");
    }
    await VerifyPreviewWorkerAsync(root, args[previewSmokeIndex + 1]);
}
if (Array.IndexOf(args, "--audio-probe") >= 0)
{
    await VerifyAudioCaptureAsync();
}
Console.WriteLine("Windows cross-platform contracts passed.");
return;

static void VerifyFrameFixtures(string path)
{
    using JsonDocument document = JsonDocument.Parse(File.ReadAllBytes(path));
    foreach (JsonElement fixture in document.RootElement.GetProperty("fixtures").EnumerateArray())
    {
        byte[] payload = fixture.TryGetProperty("payload_utf8", out JsonElement utf8)
            ? Encoding.UTF8.GetBytes(utf8.GetString()!)
            : Convert.FromHexString(fixture.GetProperty("payload_hex").GetString()!);
        MeyaFrame frame = new(
            (MeyaFrameType)fixture.GetProperty("type").GetByte(),
            payload,
            fixture.GetProperty("flags").GetUInt16(),
            Guid.Parse(fixture.GetProperty("session").GetString()!),
            fixture.GetProperty("sequence").GetUInt64());
        string actual = Convert.ToHexString(MeyaFrameCodec.Encode(frame)).ToLowerInvariant();
        Equal(fixture.GetProperty("wire_hex").GetString()!, actual, fixture.GetProperty("name").GetString()!);
    }
}

static void VerifyFragmentationAndResynchronization()
{
    Guid session = Guid.NewGuid();
    MeyaFrame expected = MeyaFrameCodec.Json(
        MeyaFrameType.Control,
        new { command = "stream_start", 文本 = "麦芽" },
        session,
        7);
    byte[] noise = "noise from a dependency\n"u8.ToArray();
    byte[] wire = [.. noise, .. MeyaFrameCodec.Encode(expected)];
    MeyaFrameDecoder decoder = new();
    List<MeyaFrame> actual = [];
    for (int index = 0; index < wire.Length; index += 7)
    {
        actual.AddRange(decoder.Feed(wire.AsSpan(index, Math.Min(7, wire.Length - index))));
    }
    Equal(1, actual.Count, "fragmentation frame count");
    Equal(noise.Length, decoder.DiscardedBytes, "discarded noise bytes");
    Equal(expected.Session, actual[0].Session, "session UUID");
    using JsonDocument json = MeyaFrameCodec.ParseJson(actual[0]);
    Equal("麦芽", json.RootElement.GetProperty("文本").GetString(), "UTF-8 JSON");
}

static void VerifyStreamingPcmConversion()
{
    WaveFormat format = new(48_000, 16, 2);
    const int inputFrames = 4_800;
    byte[] stereo = new byte[inputFrames * format.BlockAlign];
    for (int frame = 0; frame < inputFrames; frame++)
    {
        int offset = frame * format.BlockAlign;
        BinaryPrimitives.WriteInt16LittleEndian(stereo.AsSpan(offset, 2), 8_192);
        BinaryPrimitives.WriteInt16LittleEndian(stereo.AsSpan(offset + 2, 2), 8_192);
    }

    StreamingPcm16Converter converter = new(format);
    List<byte[]> output = [];
    for (int offset = 0; offset < stereo.Length; offset += 137)
    {
        output.AddRange(converter.Append(stereo.AsSpan(offset, Math.Min(137, stereo.Length - offset))));
    }
    output.AddRange(converter.Flush());
    byte[] pcm16 = output.SelectMany(chunk => chunk).ToArray();
    Equal(1_600 * sizeof(short), pcm16.Length, "48 kHz stereo to 16 kHz mono sample count");
    for (int offset = 0; offset < pcm16.Length; offset += sizeof(short))
    {
        short sample = BinaryPrimitives.ReadInt16LittleEndian(pcm16.AsSpan(offset, sizeof(short)));
        if (Math.Abs(sample - 8_192) > 1)
        {
            throw new InvalidOperationException($"PCM16 conversion amplitude: expected 8192, got {sample}");
        }
    }
    if (converter.CurrentLevel < 0.9f)
    {
        throw new InvalidOperationException($"PCM meter level: expected loud input, got {converter.CurrentLevel:F3}");
    }

    StreamingPcm16Converter silentConverter = new(format);
    silentConverter.Append(new byte[format.BlockAlign * 128]);
    if (silentConverter.CurrentLevel > 0.01f)
    {
        throw new InvalidOperationException($"PCM meter silence: expected near zero, got {silentConverter.CurrentLevel:F3}");
    }
}

static void VerifySessionTraces(string path)
{
    using JsonDocument document = JsonDocument.Parse(File.ReadAllBytes(path));
    foreach (JsonElement trace in document.RootElement.GetProperty("traces").EnumerateArray())
    {
        SessionState state = SessionState.Idle;
        List<string> states = [SessionStateMachine.WireName(state)];
        HashSet<string> actions = [];
        foreach (JsonElement item in trace.GetProperty("events").EnumerateArray())
        {
            SessionTransition transition = SessionStateMachine.Transition(
                state,
                SessionStateMachine.ParseEvent(item.GetString()!));
            state = transition.State;
            states.Add(SessionStateMachine.WireName(state));
            actions.UnionWith(transition.Actions);
        }
        string name = trace.GetProperty("name").GetString()!;
        string[] expectedStates = trace.GetProperty("states").EnumerateArray().Select(value => value.GetString()!).ToArray();
        Equal(string.Join('|', expectedStates), string.Join('|', states), $"{name} states");
        foreach (JsonElement required in trace.GetProperty("required_actions").EnumerateArray())
        {
            if (!actions.Contains(required.GetString()!))
            {
                throw new InvalidOperationException($"{name}: missing action {required.GetString()}");
            }
        }
    }
}

static string FindRoot(string start)
{
    DirectoryInfo? current = new(start);
    while (current is not null)
    {
        if (File.Exists(Path.Combine(current.FullName, "contracts", "ipc-v2-fixtures.json")))
        {
            return current.FullName;
        }
        current = current.Parent;
    }
    throw new DirectoryNotFoundException("Could not locate the Meya repository root");
}

static void Equal<T>(T expected, T actual, string context)
{
    if (!EqualityComparer<T>.Default.Equals(expected, actual))
    {
        throw new InvalidOperationException($"{context}: expected {expected}, got {actual}");
    }
}

static async Task VerifyWorkerAsync(string root, string audioPath)
{
    if (!File.Exists(audioPath))
    {
        throw new FileNotFoundException("Worker smoke WAV is missing", audioPath);
    }
    string userData = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "Meya");
    string runtime = Path.Combine(userData, "runtime");
    ModelSelection models = ModelSelection.Load(root, userData);
    await using AsrWorker worker = new(root, models.Final, "final", userData, runtime);
    await worker.StartAsync(TimeSpan.FromMinutes(3));
    TranscriptionResult result = await worker.TranscribeFinalAsync(
        audioPath,
        Guid.NewGuid(),
        TimeSpan.FromSeconds(60));
    Console.WriteLine($"Worker smoke: silence={result.Silence}, text={result.Text}");
}

static async Task VerifyPreviewWorkerAsync(string root, string audioPath)
{
    if (!File.Exists(audioPath))
    {
        throw new FileNotFoundException("Preview worker smoke WAV is missing", audioPath);
    }

    List<byte[]> pcmChunks = [];
    using (WaveFileReader reader = new(audioPath))
    {
        StreamingPcm16Converter converter = new(reader.WaveFormat);
        byte[] buffer = new byte[Math.Max(reader.WaveFormat.AverageBytesPerSecond / 10, 4096)];
        int count;
        while ((count = reader.Read(buffer, 0, buffer.Length)) > 0)
        {
            pcmChunks.AddRange(converter.Append(buffer.AsSpan(0, count)));
        }
        pcmChunks.AddRange(converter.Flush());
    }

    string userData = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "Meya");
    string runtime = Path.Combine(userData, "runtime");
    ModelSelection models = ModelSelection.Load(root, userData);
    await using AsrWorker worker = new(root, models.Preview, "preview", userData, runtime);
    await worker.StartAsync(TimeSpan.FromMinutes(3));
    if (!worker.SupportsNativeStreaming)
    {
        throw new InvalidOperationException("Preview worker did not advertise native streaming");
    }

    Guid session = Guid.NewGuid();
    await worker.StartStreamAsync(session, TimeSpan.FromSeconds(5));
    PreviewResult? last = null;
    try
    {
        foreach (byte[] chunk in pcmChunks)
        {
            last = await worker.PushPcm16Async(chunk, session, TimeSpan.FromSeconds(15));
            Console.WriteLine($"Preview revision={last.Revision}, text={last.Text}");
        }
    }
    finally
    {
        await worker.CancelStreamAsync(session, TimeSpan.FromSeconds(5));
    }
    if (last is null || last.Revision == 0)
    {
        throw new InvalidOperationException("Preview worker returned no streaming revision");
    }
}

static async Task VerifyAudioCaptureAsync()
{
    string scratch = Environment.GetEnvironmentVariable("KIROCREW_SCRATCH")
        ?? throw new InvalidOperationException("KIROCREW_SCRATCH is not configured");
    string path = Path.Combine(scratch, $"meya-audio-probe-{Guid.NewGuid():N}.wav");
    int pcmBytes = 0;
    await using AudioCapture capture = new();
    capture.Pcm16Available += chunk => pcmBytes += chunk.Length;
    capture.Start(path);
    await Task.Delay(1200);
    CapturedAudio result = await capture.StopAsync();
    pcmBytes += result.FinalPcm16.Length;
    if (result.Duration < TimeSpan.FromMilliseconds(500) || pcmBytes == 0 || !File.Exists(path))
    {
        throw new InvalidOperationException(
            $"Audio probe failed duration={result.Duration.TotalSeconds:F2}s pcmBytes={pcmBytes}");
    }
    Console.WriteLine(
        $"Audio probe: duration={result.Duration.TotalSeconds:F2}s, pcm16Bytes={pcmBytes}, path={path}");
}
