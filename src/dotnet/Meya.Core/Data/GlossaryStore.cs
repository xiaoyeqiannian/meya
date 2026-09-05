using System.Collections.ObjectModel;
using System.Text;
using System.Text.Json;

namespace Meya.Core;

public sealed class GlossaryEntry
{
    public string Canonical { get; set; } = string.Empty;
    public string Pronunciations { get; set; } = string.Empty;
    public string Corrections { get; set; } = string.Empty;
    public string Suggestion { get; set; } = string.Empty;
    public string HotwordStatus { get; set; } = "— 待检测";
}

public sealed class GlossaryStore
{
    public const int MaximumEntries = 100;
    private readonly string _userDataDirectory;
    private readonly string _projectDirectory;

    public GlossaryStore(string userDataDirectory, string projectDirectory)
    {
        _userDataDirectory = userDataDirectory;
        _projectDirectory = projectDirectory;
    }

    public string GlossaryPath => Path.Combine(_userDataDirectory, "glossary.tsv");
    public string TermsPath => Path.Combine(_userDataDirectory, "terms.txt");
    public string CorrectionsPath => Path.Combine(_userDataDirectory, "corrections.tsv");

    public ObservableCollection<GlossaryEntry> Load()
    {
        List<GlossaryEntry> entries = File.Exists(GlossaryPath)
            ? Parse(File.ReadAllLines(GlossaryPath, Encoding.UTF8), Path.GetExtension(GlossaryPath))
            : LoadLegacy();
        IReadOnlyList<GlossaryEntry> normalized = Normalize(entries);
        ApplyHotwordReport(normalized);
        return new ObservableCollection<GlossaryEntry>(normalized);
    }

    public IReadOnlyList<GlossaryEntry> Import(string path) =>
        Normalize(Parse(File.ReadAllLines(path, Encoding.UTF8), Path.GetExtension(path)));

    public void Save(IEnumerable<GlossaryEntry> source)
    {
        IReadOnlyList<GlossaryEntry> entries = Normalize(source).Take(MaximumEntries).ToArray();
        Directory.CreateDirectory(_userDataDirectory);
        List<string> glossary = ["# 标准写法\t发音/近音别名（、分隔）\t常见识别错词（、分隔）"];
        glossary.AddRange(entries.Select(entry =>
            $"{entry.Canonical}\t{JoinVariants(entry.Pronunciations)}\t{JoinVariants(entry.Corrections)}"));
        WriteAtomic(GlossaryPath, glossary);

        List<string> terms = ["# 每行一个术语；保存后下一次识别立即生效"];
        int characters = 0;
        foreach (string term in entries.SelectMany(entry =>
                     new[] { entry.Canonical }.Concat(SplitVariants(entry.Pronunciations)))
                 .Distinct(StringComparer.OrdinalIgnoreCase))
        {
            if (terms.Count > 100 || characters + term.Length > 1000)
            {
                break;
            }
            terms.Add(term);
            characters += term.Length;
        }
        WriteAtomic(TermsPath, terms);

        List<string> corrections = ["# 常见错词\t正确写法"];
        foreach (GlossaryEntry entry in entries)
        {
            corrections.AddRange(SplitVariants(entry.Corrections).Select(value => $"{value}\t{entry.Canonical}"));
        }
        WriteAtomic(CorrectionsPath, corrections);
    }

    private void ApplyHotwordReport(IReadOnlyList<GlossaryEntry> entries)
    {
        string path = Path.Combine(_userDataDirectory, "runtime", "hotword-catalog-report.json");
        if (!File.Exists(path))
        {
            return;
        }
        try
        {
            using JsonDocument document = JsonDocument.Parse(File.ReadAllBytes(path));
            if (!document.RootElement.TryGetProperty("entries", out JsonElement reports) || reports.ValueKind != JsonValueKind.Array)
            {
                return;
            }
            Dictionary<string, GlossaryEntry> byName = entries.ToDictionary(
                entry => entry.Canonical,
                StringComparer.OrdinalIgnoreCase);
            foreach (JsonElement report in reports.EnumerateArray())
            {
                string canonical = report.TryGetProperty("canonical", out JsonElement name)
                    ? name.GetString() ?? string.Empty
                    : string.Empty;
                if (!byName.TryGetValue(canonical, out GlossaryEntry? entry))
                {
                    continue;
                }
                string status = report.TryGetProperty("status", out JsonElement state)
                    ? state.GetString() ?? string.Empty
                    : string.Empty;
                entry.HotwordStatus = status switch
                {
                    "effective" => "● 已生效",
                    "partial_unknown" => "◐ 部分无效",
                    "unknown" => "○ 需要发音",
                    _ => "— 待检测",
                };
                if (report.TryGetProperty("pronunciation_suggestions", out JsonElement suggestions)
                    && suggestions.ValueKind == JsonValueKind.Array)
                {
                    entry.Suggestion = string.Join('、', suggestions.EnumerateArray()
                        .Where(value => value.ValueKind == JsonValueKind.String)
                        .Select(value => value.GetString())
                        .Where(value => !string.IsNullOrWhiteSpace(value))!);
                }
            }
        }
        catch (JsonException)
        {
        }
    }

    private List<GlossaryEntry> LoadLegacy()
    {
        List<GlossaryEntry> entries = [];
        string terms = File.Exists(TermsPath)
            ? TermsPath
            : Path.Combine(_projectDirectory, "terms.txt");
        if (File.Exists(terms))
        {
            entries.AddRange(Parse(File.ReadAllLines(terms, Encoding.UTF8), ".txt"));
        }
        string corrections = File.Exists(CorrectionsPath)
            ? CorrectionsPath
            : Path.Combine(_projectDirectory, "corrections.tsv");
        if (File.Exists(corrections))
        {
            foreach (string raw in File.ReadLines(corrections, Encoding.UTF8))
            {
                if (string.IsNullOrWhiteSpace(raw) || raw.TrimStart().StartsWith('#'))
                {
                    continue;
                }
                string[] columns = raw.Split('\t');
                if (columns.Length < 2)
                {
                    continue;
                }
                string observed = columns[0].Trim();
                string canonical = columns[1].Trim();
                GlossaryEntry? existing = entries.FirstOrDefault(entry =>
                    string.Equals(entry.Canonical, canonical, StringComparison.OrdinalIgnoreCase));
                if (existing is null)
                {
                    entries.Add(new GlossaryEntry { Canonical = canonical, Corrections = observed });
                }
                else
                {
                    existing.Corrections = JoinVariants(existing.Corrections, observed);
                }
            }
        }
        return entries;
    }

    private static List<GlossaryEntry> Parse(IEnumerable<string> lines, string extension)
    {
        List<GlossaryEntry> entries = [];
        foreach (string raw in lines)
        {
            string line = raw.Trim();
            if (line.Length == 0 || line.StartsWith('#'))
            {
                continue;
            }
            char delimiter = extension.Equals(".csv", StringComparison.OrdinalIgnoreCase) ? ',' : '\t';
            string[] columns = line.Split(delimiter);
            entries.Add(new GlossaryEntry
            {
                Canonical = columns[0].Trim().Trim('"'),
                Pronunciations = columns.Length > 1 ? columns[1].Trim().Trim('"') : string.Empty,
                Corrections = columns.Length > 2 ? columns[2].Trim().Trim('"') : string.Empty,
            });
        }
        return entries;
    }

    private static IReadOnlyList<GlossaryEntry> Normalize(IEnumerable<GlossaryEntry> source)
    {
        Dictionary<string, GlossaryEntry> entries = new(StringComparer.OrdinalIgnoreCase);
        foreach (GlossaryEntry value in source)
        {
            string canonical = value.Canonical.Trim();
            if (canonical.Length == 0)
            {
                continue;
            }
            if (!entries.TryGetValue(canonical, out GlossaryEntry? entry))
            {
                entry = new GlossaryEntry { Canonical = canonical };
                entries[canonical] = entry;
            }
            entry.Pronunciations = JoinVariants(entry.Pronunciations, value.Pronunciations);
            entry.Corrections = JoinVariants(entry.Corrections, value.Corrections);
            entry.Suggestion = value.Suggestion.Trim();
            entry.HotwordStatus = string.IsNullOrWhiteSpace(value.HotwordStatus) ? "— 待检测" : value.HotwordStatus;
        }
        return entries.Values.Take(MaximumEntries).ToArray();
    }

    private static IEnumerable<string> SplitVariants(string value) =>
        value.Split(['、', ',', '，', ';', '；', '|'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Where(item => item.Length > 0);

    private static string JoinVariants(params string[] values) =>
        string.Join('、', values.SelectMany(SplitVariants).Distinct(StringComparer.OrdinalIgnoreCase));

    private static void WriteAtomic(string path, IEnumerable<string> lines)
    {
        string temporary = path + ".tmp";
        File.WriteAllLines(temporary, lines, new UTF8Encoding(false));
        File.Move(temporary, path, overwrite: true);
    }
}
