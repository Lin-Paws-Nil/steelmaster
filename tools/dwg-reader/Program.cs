using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using ACadSharp;
using ACadSharp.Entities;
using ACadSharp.IO;
using ACadSharp.Tables;

/// <summary>
/// Reads a DWG/DXF file using ACadSharp and outputs structural data as JSON.
/// Used by the Steel Estimator Python backend.
/// </summary>
class Program
{
    static int Main(string[] args)
    {
        if (args.Length < 1)
        {
            Console.Error.WriteLine("Usage: dwg-reader <filepath>");
            return 1;
        }

        string filepath = args[0];
        if (!File.Exists(filepath))
        {
            Console.Error.WriteLine($"File not found: {filepath}");
            return 1;
        }

        try
        {
            CadDocument doc;
            string ext = Path.GetExtension(filepath).ToLower();

            if (ext == ".dwg")
            {
                using var reader = new DwgReader(filepath);
                doc = reader.Read();
            }
            else if (ext == ".dxf")
            {
                using var reader = new DxfReader(filepath);
                doc = reader.Read();
            }
            else
            {
                Console.Error.WriteLine("Unsupported file format. Use .dwg or .dxf");
                return 1;
            }

            var result = ExtractStructuralData(doc, filepath);
            string json = JsonSerializer.Serialize(result, new JsonSerializerOptions
            {
                WriteIndented = true,
                PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            });

            Console.WriteLine(json);
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"Error reading file: {ex.Message}");
            return 2;
        }
    }

    static Dictionary<string, object> ExtractStructuralData(CadDocument doc, string filepath)
    {
        var layers = new List<Dictionary<string, object>>();
        var textEntities = new List<Dictionary<string, object>>();
        var dimensions = new List<Dictionary<string, object>>();
        var polylines = new List<Dictionary<string, object>>();
        var blocks = new List<Dictionary<string, object>>();
        var allEntities = new List<Dictionary<string, string>>();

        // Extract layers
        foreach (var layer in doc.Layers)
        {
            layers.Add(new Dictionary<string, object>
            {
                ["name"] = layer.Name,
                ["color"] = layer.Color.Index,
                ["lineWeight"] = layer.LineWeight.ToString(),
            });
        }

        // Extract entities from model space
        var modelSpace = doc.ModelSpace;
        if (modelSpace != null)
        {
            foreach (var entity in modelSpace.Entities)
            {
                allEntities.Add(new Dictionary<string, string>
                {
                    ["type"] = entity.GetType().Name,
                    ["layer"] = entity.Layer?.Name ?? "",
                });

                switch (entity)
                {
                    case TextEntity text:
                        textEntities.Add(new Dictionary<string, object>
                        {
                            ["text"] = text.Value ?? "",
                            ["layer"] = text.Layer?.Name ?? "",
                            ["x"] = Math.Round(text.InsertPoint.X, 2),
                            ["y"] = Math.Round(text.InsertPoint.Y, 2),
                            ["height"] = Math.Round(text.Height, 2),
                        });
                        break;

                    case MText mtext:
                        textEntities.Add(new Dictionary<string, object>
                        {
                            ["text"] = mtext.PlainText ?? mtext.Value ?? "",
                            ["layer"] = mtext.Layer?.Name ?? "",
                            ["x"] = Math.Round(mtext.InsertPoint.X, 2),
                            ["y"] = Math.Round(mtext.InsertPoint.Y, 2),
                            ["height"] = Math.Round(mtext.Height, 2),
                        });
                        break;

                    case DimensionLinear dim:
                        dimensions.Add(new Dictionary<string, object>
                        {
                            ["type"] = "linear",
                            ["text"] = dim.Text ?? "",
                            ["measurement"] = Math.Round(dim.Measurement, 2),
                            ["layer"] = dim.Layer?.Name ?? "",
                        });
                        break;

                    case DimensionAligned dim:
                        dimensions.Add(new Dictionary<string, object>
                        {
                            ["type"] = "aligned",
                            ["text"] = dim.Text ?? "",
                            ["measurement"] = Math.Round(dim.Measurement, 2),
                            ["layer"] = dim.Layer?.Name ?? "",
                        });
                        break;

                    case LwPolyline poly:
                        if (poly.IsClosed && poly.Vertices.Count() >= 4)
                        {
                            var xs = poly.Vertices.Select(v => v.Location.X).ToList();
                            var ys = poly.Vertices.Select(v => v.Location.Y).ToList();
                            double width = Math.Round(xs.Max() - xs.Min(), 2);
                            double height = Math.Round(ys.Max() - ys.Min(), 2);

                            if (width > 0 && height > 0)
                            {
                                polylines.Add(new Dictionary<string, object>
                                {
                                    ["layer"] = poly.Layer?.Name ?? "",
                                    ["width"] = width,
                                    ["height"] = height,
                                    ["vertexCount"] = poly.Vertices.Count(),
                                    ["isClosed"] = true,
                                });
                            }
                        }
                        break;

                    case Insert insert:
                        blocks.Add(new Dictionary<string, object>
                        {
                            ["blockName"] = insert.Block?.Name ?? "",
                            ["layer"] = insert.Layer?.Name ?? "",
                            ["x"] = Math.Round(insert.InsertPoint.X, 2),
                            ["y"] = Math.Round(insert.InsertPoint.Y, 2),
                            ["scaleX"] = Math.Round(insert.XScale, 4),
                            ["scaleY"] = Math.Round(insert.YScale, 4),
                        });
                        break;
                }
            }
        }

        // Count entity types
        var entityTypeCounts = allEntities
            .GroupBy(e => e["type"])
            .ToDictionary(g => g.Key, g => g.Count());

        return new Dictionary<string, object>
        {
            ["filename"] = Path.GetFileName(filepath),
            ["version"] = doc.Header.Version.ToString(),
            ["layerCount"] = layers.Count,
            ["layers"] = layers,
            ["textEntities"] = textEntities,
            ["dimensions"] = dimensions,
            ["polylines"] = polylines,
            ["blocks"] = blocks,
            ["entityTypeCounts"] = entityTypeCounts,
            ["totalEntities"] = allEntities.Count,
        };
    }
}
