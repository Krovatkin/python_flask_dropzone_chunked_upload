from pathlib import Path
from threading import Lock
from collections import defaultdict
import shutil
import uuid

import werkzeug
from werkzeug.utils import secure_filename
from flask import Flask, request, send_from_directory
from werkzeug.exceptions import abort

app = Flask(__name__)

storage_path: Path = Path(__file__).parent / "storage"
chunk_path: Path = Path(__file__).parent / "chunk"

allow_downloads = True
dropzone_cdn = "https://cdnjs.cloudflare.com/ajax/libs/dropzone"
dropzone_version = "5.7.6"
dropzone_timeout = "120000"
dropzone_max_file_size = "100000"
dropzone_chunk_size = "1000000"
dropzone_parallel_chunks = "true"
dropzone_force_chunking = "true"
host = "0.0.0.0"
port = 16273

lock = Lock()
chucks = defaultdict(list)


@app.errorhandler(werkzeug.exceptions.InternalServerError)
def handle_500(e):
    response = e.get_response()
    response.status = 500
    response.body = f"Error: {e}"
    return response


@app.get("/")
def index():
    index_file = Path(__file__) / "index.html"
    if index_file.exists():
        return index_file.read_text()
    return f"""
<!doctype html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <link rel="stylesheet" href="{dropzone_cdn.rstrip('/')}/{dropzone_version}/min/dropzone.min.css"/>
    <link rel="stylesheet" href="{dropzone_cdn.rstrip('/')}/{dropzone_version}/min/basic.min.css"/>
    <script type="application/javascript"
        src="{dropzone_cdn.rstrip('/')}/{dropzone_version}/min/dropzone.min.js">
    </script>
    <title>pyfiledrop</title>
</head>
<body>

    <div id="content" style="width: 800px; margin: 0 auto;">
        <h2>Upload new files</h2>
        <form method="POST" action='/upload' class="dropzone dz-clickable" id="dropper" enctype="multipart/form-data">
        </form>

        <h2>
            Uploaded
        </h2>
        <div id="uploaded">

        </div>

        <script type="application/javascript">

            function init() {{

                Dropzone.options.dropper = {{
                    paramName: 'file',
                    chunking: true,
                    forceChunking: {dropzone_force_chunking},
                    url: '/upload',
                    retryChunks: true,
                    parallelChunkUploads: {dropzone_parallel_chunks},
                    timeout: {dropzone_timeout}, // microseconds
                    maxFilesize: {dropzone_max_file_size}, // megabytes
                    chunkSize: {dropzone_chunk_size}, // bytes
                    init: function () {{
                        this.on("complete", function (file) {{
                            let combo = `${{file.upload.filename}} (uuid: ${{file.upload.uuid}})`;
                            document.getElementById("uploaded").innerHTML += combo  + "<br />";
                        }});
                    }}
                }}

            }}

            init();

        </script>
    </div>
</body>
</html>
    """

@app.post("/upload")
def upload():
    file = request.files.get("file")
    if not file:
        abort(400, f"No file provided")

    dz_uuid = request.form.get("dzuuid")
    if not dz_uuid:
        # Assume this file has not been chunked
        with open(storage_path / f"{uuid.uuid4()}_{secure_filename(file.filename)}", "wb") as f:
            file.save(f)
        return "File Saved"

    # Chunked download
    try:
        current_chunk = int(request.form["dzchunkindex"])
        total_chunks = int(request.form["dztotalchunkcount"])
    except KeyError as err:
        raise abort(400, body=f"Not all required fields supplied, missing {err}")
    except ValueError:
        raise abort(400, body=f"Values provided were not in expected format")

    save_dir = chunk_path / dz_uuid

    if not save_dir.exists():
        save_dir.mkdir(exist_ok=True, parents=True)

    # Save the individual chunk
    with open(save_dir / str(request.form["dzchunkindex"]), "wb") as f:
        file.save(f)

    # See if we have all the chunks downloaded
    with lock:
        chucks[dz_uuid].append(current_chunk)
        completed = len(chucks[dz_uuid]) == total_chunks

    # Concat all the files into the final file when all are downloaded
    if completed:
        with open(storage_path / f"{dz_uuid}_{secure_filename(file.filename)}", "wb") as f:
            for file_number in range(total_chunks):
                f.write((save_dir / str(file_number)).read_bytes())
        print(f"{file.filename} has been uploaded")
        shutil.rmtree(save_dir)

    return "Chunk upload successful"


@app.route("/download/<dz_uuid>")
def download(dz_uuid):
    if not allow_downloads:
        raise abort(403)
    for file in storage_path.iterdir():
        if file.is_file() and file.name.startswith(dz_uuid):
            return send_from_directory(file.parent.absolute(), file.name, as_attachment=True)
    return abort(404)

if __name__ == "__main__":
    try:
        if int(dropzone_timeout) < 1 or int(dropzone_chunk_size) < 1 or int(dropzone_max_file_size) < 1:
            raise Exception("Invalid dropzone option, make sure max-size, timeout, and chunk-size are all positive")
    except ValueError:
        raise Exception("Invalid dropzone option, make sure max-size, timeout, and chunk-size are all integers")


    if not storage_path.exists():
        storage_path.mkdir(exist_ok=True)
    if not chunk_path.exists():
        chunk_path.mkdir(exist_ok=True)

    print(
        f"""Timeout: {int(dropzone_timeout) // 1000} seconds per chunk
Chunk Size: {int(dropzone_chunk_size) // 1024} Kb
Max File Size: {int(dropzone_max_file_size)} Mb
Force Chunking: {dropzone_force_chunking}
Parallel Chunks: {dropzone_parallel_chunks}
Storage Path: {storage_path.absolute()}
Chunk Path: {chunk_path.absolute()}
"""
    )

    app.run(host=host, port=port)