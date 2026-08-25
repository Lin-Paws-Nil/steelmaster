# Steel Estimator

A web application that estimates reinforcement steel requirements for building construction from structural drawings (DWG/DXF files).

## Features

- **DWG/DXF File Upload**: Upload AutoCAD structural drawings and automatically detect structural elements
- **Intelligent Parsing**: Extracts columns, beams, slabs, footings, staircases, and lintels from drawings
- **AI-Enhanced Detection**: Optional LLM integration to interpret complex drawings when automated parsing isn't enough
- **Manual Input**: Manually specify structural elements when drawings aren't available
- **Steel Estimation Engine**: Calculates reinforcement based on IS 456:2000 guidelines including:
  - Main bars with lap lengths
  - Stirrups/ties with hook allowances
  - Distribution steel for slabs
  - Extra/cranked bars at supports
- **Detailed Reports**: Breakdown by element type, bar diameter, and individual element

## Setup

### Prerequisites

- Python 3.10+
- (Optional) ODA File Converter or LibreDWG for native .dwg file support
- (Optional) OpenAI API key for AI-enhanced interpretation

### Installation

```bash
cd "Steel Estimator"
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Running

```bash
python run.py
```

The app will be available at **http://localhost:8000**

### Environment Variables (Optional)

Set these for LLM-enhanced drawing interpretation:

- `LLM_API_KEY` - Your OpenAI (or compatible) API key
- `LLM_API_BASE` - API base URL (default: https://api.openai.com/v1)
- `LLM_MODEL` - Model name (default: gpt-4o)

## Usage

### Option 1: Upload Drawing File

1. Open http://localhost:8000
2. Drag & drop your .dwg or .dxf structural drawing
3. Enable "Use AI to enhance detection" if you have an API key configured
4. View the detailed steel estimation report

### Option 2: Manual Input

1. Click the "Manual Input" tab
2. Add structural elements (columns, beams, slabs, etc.) with dimensions
3. Specify reinforcement details if known (or leave blank for standard defaults)
4. Click "Calculate Steel Estimate"

## How It Works

1. **File Parsing**: The DXF/DWG parser reads layers, text annotations, and geometric entities
2. **Element Detection**: Structural elements are identified by layer names, text labels, and dimensions
3. **LLM Enhancement** (optional): An AI model interprets ambiguous data and fills in missing specifications
4. **Steel Calculation**: Based on IS 456:2000 standards:
   - Lap length = 50d for Fe500 in tension
   - Hook allowance = 10d
   - Standard clear covers by element type
   - Stirrup perimeter calculations with hooks

## Supported Elements

| Element | Main Steel | Secondary Steel |
|---------|-----------|-----------------|
| Column | Vertical bars + ties | - |
| Beam | Top + bottom bars + stirrups | Extra bars at supports |
| Slab | Main bars + distribution | Top extra at supports |
| Footing | Both directions | - |
| Staircase | Main + distribution (inclined) | - |
| Lintel | Same as beam | - |

## DWG File Support

For native .dwg file processing, install one of:

- **ODA File Converter** (recommended): Download from https://www.opendesign.com/guestfiles/oda_file_converter
- **LibreDWG**: Install via package manager (`brew install libredwg` on macOS)

Without these, convert your .dwg files to .dxf format using AutoCAD or a free online converter before uploading.

## API Endpoints

- `POST /api/upload` - Upload and parse a drawing file
- `POST /api/upload-and-estimate` - Upload, parse, and estimate in one step
- `POST /api/manual-estimate` - Estimate from manually provided elements
- `POST /api/estimate` - Estimate from a list of structural elements
- `POST /api/estimate-single` - Estimate a single element
- `GET /health` - Health check
