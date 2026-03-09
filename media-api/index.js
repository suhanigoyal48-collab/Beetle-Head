const express = require("express");
const axios = require("axios");
const cors = require("cors");
const { uploadFile } = require("./r2_storage");

const app = express();
app.use(cors({ origin: "*" }))
app.use(express.json())

app.post("/media/upload", async (req, res) => {
    try {
        const { file_data, filename, file_type } = req.body;
        if (!file_data || !filename) {
            return res.status(400).json({ error: "Missing file data or filename" });
        }

        // Decode base64
        const fileBuffer = Buffer.from(file_data, "base64");

        const result = await uploadFile(fileBuffer, filename, file_type);

        if (result.success) {
            res.json({ status: "success", file_url: result.fileUrl });
        } else {
            res.status(500).json({ status: "error", message: result.error });
        }
    } catch (error) {
        console.error("Upload endpoint error:", error);
        res.status(500).json({ error: "Internal server error" });
    }
}); 

async function getImageBase64(imageUrl) {
    const imageBuffer = await axios.get(imageUrl,{
        responseType: "arraybuffer"
    });
    return Buffer.from(imageBuffer.data).toString("base64");
}
app.post("/chat", async (req, res) => {
    try {
        console.log("hitting api")
        const {image_url,model,stream} = req.body;
        const modelName = model || "gemma3:4b";
        const streamBool = stream || true;
        let imageBase64;
        if(image_url){
            imageBase64 = await getImageBase64(image_url);
        }
        if (imageBase64 && req.body.messages?.length) {
            req.body.messages[req.body.messages.length - 1].images = [imageBase64];
        }
        req.body.model = modelName;
        req.body.stream = streamBool;
        const response = await axios.post("http://localhost:11434/api/chat", req.body, {
            responseType: "stream"  
        });
        // console.log("data",response.data)
        res.setHeader("Content-Type", "application/json");
        // res.setHeader("Transfer-Encoding", "chunked");

        // 🔥 pipe streaming chunks
        // for await (const chunk of response.data) {
        // res.write(chunk.message.content);
        // }
        response.data.pipe(res);
        // res.end();
    } catch (error) {
        console.error(error.message);
        res.status(500).json({ error: "Failed to upload file" });
    }
});

app.listen(3000, () => {
    console.log("Server started on port 3000");
});
