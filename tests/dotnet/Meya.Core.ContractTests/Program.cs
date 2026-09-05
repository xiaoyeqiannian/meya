using System.Text;
using System.Text.Json;
using Meya.Core;

Console.OutputEncoding = new UTF8Encoding(false);

string root = args.Length > 0 ? Path.GetFullPath(args[0]) : FindRoot(AppContext.BaseDirectory);
VerifyFrameFixtures(Path.Combine(root, "contracts", "ipc-v2-fixtures.json"));
VerifyFragmentationAndResynchronization();
VerifySessionTraces(Path.Combine(root, "contracts", "session-traces.json"));
VerifyMenuContract();
VerifyOverlayPresentation();
VerifyGlossaryStore();
Console.WriteLine("Meya shared .NET contracts passed.");
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

static void VerifyMenuContract()
{
    IReadOnlyList<MenuEntry> menu = MeyaMenu.Create("1.2.3", "● 麦芽已就绪 · 长按右 Ctrl 输入", false);
    Equal("麦芽 Meya · v1.2.3", menu[0].Label, "menu version");
    Equal("● 麦芽已就绪 · 长按右 Ctrl 输入", menu[1].Label, "menu status");
    Equal("暂无可学习的修改", menu[3].Label, "menu disabled learning");
    Equal(false, menu[3].Enabled, "menu disabled learning state");
    Equal("管理个人词库…", menu[4].Label, "menu glossary");
    Equal("管理识别模型…", menu[5].Label, "menu models");
    Equal("更多", menu[7].Label, "menu more");
    Equal("打开录音目录…", menu[7].Children![0].Label, "menu recordings");
    Equal("权限与诊断…", menu[7].Children![2].Label, "menu diagnostics");
    Equal("退出麦芽 Meya", menu[9].Label, "menu exit");

    menu = MeyaMenu.Create("1.2.3", "ready", true);
    Equal("学习刚才的修改", menu[3].Label, "menu enabled learning");
    Equal(true, menu[3].Enabled, "menu enabled learning state");
}

static void VerifyOverlayPresentation()
{
    OverlayPresentation recording = OverlayPresentation.Recording("右 Ctrl", true);
    Equal(OverlayPhase.Recording, recording.Phase, "overlay recording phase");
    Equal("正在听 · 松开右 Ctrl 完成", recording.Text, "overlay recording text");

    OverlayPresentation draft = OverlayPresentation.Draft(new string('麦', 120), "右 Ctrl");
    Equal(OverlayPhase.Draft, draft.Phase, "overlay draft phase");
    Equal(OverlayPresentation.MaximumVisibleCharacters, draft.Text.Length, "overlay latest text length");
    Equal("实时识别 · 松开右 Ctrl 完成", draft.Status, "overlay draft status");

    OverlayPresentation final = OverlayPresentation.Final("最终结果", false);
    Equal("识别结果已复制", final.Status, "overlay copied status");
}

static void VerifyGlossaryStore()
{
    string scratchRoot = Environment.GetEnvironmentVariable("KIROCREW_SCRATCH") ?? Path.GetTempPath();
    string root = Path.Combine(scratchRoot, $"meya-glossary-contract-{Guid.NewGuid():N}");
    string user = Path.Combine(root, "user");
    string project = Path.Combine(root, "project");
    Directory.CreateDirectory(project);
    try
    {
        GlossaryStore store = new(user, project);
        store.Save(
        [
            new GlossaryEntry
            {
                Canonical = "NovaKit",
                Pronunciations = "诺瓦套件、nova kit",
                Corrections = "诺瓦、nova cat",
            },
            new GlossaryEntry { Canonical = "K8s", Pronunciations = "K 八 S" },
        ]);
        IReadOnlyList<GlossaryEntry> loaded = store.Load();
        Equal(2, loaded.Count, "glossary entry count");
        Equal("NovaKit", loaded[0].Canonical, "glossary canonical");
        Equal("诺瓦套件、nova kit", loaded[0].Pronunciations, "glossary pronunciations");
        string terms = File.ReadAllText(store.TermsPath);
        if (!terms.Contains("NovaKit") || !terms.Contains("诺瓦套件"))
        {
            throw new InvalidOperationException("compiled terms are incomplete");
        }
        string corrections = File.ReadAllText(store.CorrectionsPath);
        if (!corrections.Contains("诺瓦\tNovaKit"))
        {
            throw new InvalidOperationException("compiled corrections are incomplete");
        }
    }
    finally
    {
        if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
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
