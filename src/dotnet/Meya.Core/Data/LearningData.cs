using System.Text;
using System.Text.Json;

namespace Meya.Core;

public sealed class LearningRuleItem
{
    public int Id { get; init; }
    public string Observed { get; init; } = string.Empty;
    public string Canonical { get; init; } = string.Empty;
    public int Confirmations { get; init; }
    public int HitCount { get; init; }
    public bool Activated { get; init; }
    public string Evidence { get; init; } = string.Empty;
    public string UpdatedAt { get; init; } = string.Empty;
    public string State => Activated ? "● 已生效" : "○ 待确认";
}

public sealed class TrainingSampleItem
{
    public string SampleId { get; init; } = string.Empty;
    public string CreatedAt { get; init; } = string.Empty;
    public string Audio { get; init; } = string.Empty;
    public string Reference { get; init; } = string.Empty;
    public string Hypothesis { get; init; } = string.Empty;
    public string Model { get; init; } = string.Empty;
    public string LabelStatus { get; init; } = string.Empty;
    public string StatusText => LabelStatus == "user_confirmed" ? "● 可训练" : "◐ 待复核";
}

public sealed class TrainingDataStore
{
    private readonly string _root;
    private readonly string _manifest;

    public TrainingDataStore(string userDataDirectory)
    {
        _root = Path.Combine(userDataDirectory, "training-data");
        _manifest = Path.Combine(_root, "samples.jsonl");
    }

    public IReadOnlyList<TrainingSampleItem> Load()
    {
        if (!File.Exists(_manifest))
        {
            return [];
        }
        List<TrainingSampleItem> samples = [];
        foreach (string line in File.ReadLines(_manifest, Encoding.UTF8))
        {
            try
            {
                using JsonDocument document = JsonDocument.Parse(line);
                JsonElement value = document.RootElement;
                samples.Add(new TrainingSampleItem
                {
                    SampleId = Get(value, "sample_id"),
                    CreatedAt = Get(value, "created_at"),
                    Audio = Get(value, "audio"),
                    Reference = Get(value, "reference", Get(value, "reference_candidate")),
                    Hypothesis = Get(value, "hypothesis"),
                    Model = Get(value, "model"),
                    LabelStatus = Get(value, "label_status"),
                });
            }
            catch (JsonException)
            {
            }
        }
        return samples.OrderByDescending(item => item.CreatedAt).ToArray();
    }

    public void Delete(string sampleId)
    {
        IReadOnlyList<string> lines = File.Exists(_manifest)
            ? File.ReadAllLines(_manifest, Encoding.UTF8)
            : [];
        List<string> kept = [];
        string? audio = null;
        foreach (string line in lines)
        {
            try
            {
                using JsonDocument document = JsonDocument.Parse(line);
                if (Get(document.RootElement, "sample_id") == sampleId)
                {
                    audio = Get(document.RootElement, "audio");
                    continue;
                }
            }
            catch (JsonException)
            {
            }
            kept.Add(line);
        }
        Directory.CreateDirectory(_root);
        File.WriteAllLines(_manifest, kept, new UTF8Encoding(false));
        if (!string.IsNullOrWhiteSpace(audio))
        {
            string path = Path.GetFullPath(Path.Combine(_root, audio));
            if (path.StartsWith(Path.GetFullPath(_root), StringComparison.OrdinalIgnoreCase))
            {
                File.Delete(path);
            }
        }
    }

    private static string Get(JsonElement value, string name, string fallback = "") =>
        value.TryGetProperty(name, out JsonElement property) && property.ValueKind == JsonValueKind.String
            ? property.GetString() ?? fallback
            : fallback;
}
