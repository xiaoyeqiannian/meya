using System.Text.Json;

namespace Meya.Windows;

internal sealed record ModelSelection(string Preview, string Final)
{
    internal const string DefaultPreview = "paraformer:iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online";
    internal const string DefaultFinal = "paraformer:iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch";

    internal static ModelSelection Load(string projectRoot, string userDataDirectory)
    {
        string userPath = Path.Combine(userDataDirectory, "model-config.json");
        string path = File.Exists(userPath) ? userPath : Path.Combine(projectRoot, "model-config.json");
        try
        {
            using JsonDocument document = JsonDocument.Parse(File.ReadAllBytes(path));
            JsonElement root = document.RootElement;
            string? legacy = Get(root, "model");
            string final = Get(root, "final_model") ?? legacy ?? DefaultFinal;
            string preview = Get(root, "preview_model") ?? final;
            return new ModelSelection(preview, final);
        }
        catch
        {
            return new ModelSelection(DefaultPreview, DefaultFinal);
        }
    }

    internal void Save(string userDataDirectory)
    {
        Directory.CreateDirectory(userDataDirectory);
        string path = Path.Combine(userDataDirectory, "model-config.json");
        string temporary = path + ".tmp";
        byte[] payload = JsonSerializer.SerializeToUtf8Bytes(new Dictionary<string, string>
        {
            ["preview_model"] = Preview,
            ["final_model"] = Final,
        }, new JsonSerializerOptions { WriteIndented = true });
        File.WriteAllBytes(temporary, payload);
        File.Move(temporary, path, overwrite: true);
    }

    private static string? Get(JsonElement root, string name)
    {
        if (!root.TryGetProperty(name, out JsonElement value) || value.ValueKind != JsonValueKind.String)
        {
            return null;
        }
        string? text = value.GetString()?.Trim();
        return string.IsNullOrEmpty(text) ? null : text;
    }
}
