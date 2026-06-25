# 目标检测标注工具 (label_for_detect)

基于 Python + Tkinter 的桌面端目标检测标注工具，支持 YOLO 格式标注、YOLOv8 预标注、按分类筛选图片，以及常用标注编辑操作。

## 功能概览

- **图片管理**：打开目录递归扫描图片，缩略图列表，上一张/下一张/跳转
- **标注编辑**：绘制、移动、缩放、删除锚框；撤销/重做；多选与框选
- **标签管理**：加载 `classes.txt` 或按子文件夹名导入；支持导出标签文件
- **分类筛选**：全部 / 已标注 / 未标注 / 不确定（手动分类可持久化）
- **预标注**：单图预标注、按当前筛选批量预标注（YOLOv8 / Ultralytics）
- **导出**：YOLO、COCO、Pascal VOC
- **会话恢复**：记住上次打开的目录与图片位置

## 环境要求

- Windows 10/11（推荐）
- Python 3.10+
- 预标注可选：NVIDIA GPU + CUDA（`.pt`）；或使用 `.onnx` 模型

## 安装与运行

```bash
pip install -r requirements.txt
python main.py
```

Windows 下也可双击 `start.bat` 或 `start.vbs`（无控制台窗口启动）。

可选参数：

```bash
python main.py --dir "D:/images" --weights "model.pt"
```

## 标注文件说明

- 每张图片旁生成同名 `.txt`（YOLO 格式：`class_id cx cy w h`）
- **已标注**：存在 `.txt`（可为空文件，表示背景/负样本）
- **未标注**：无 `.txt`
- **不确定**：根目录 `.annotation_status.json` 中记录（仅此状态需要额外文件）

打开目录时优先读取根目录 `classes.txt`；若不存在，再尝试按「每类一个子文件夹」结构自动导入标签。

## 常用快捷键

| 快捷键 | 功能 |
|--------|------|
| Ctrl+O | 打开目录 |
| Ctrl+S | 保存当前图标注 |
| Ctrl+Shift+S | 导出数据集 |
| Ctrl+Z / Ctrl+Y | 撤销 / 重做 |
| Delete | 删除选中锚框 |
| ←/→ 或 A/D | 上一张 / 下一张 |
| Ctrl+G | 跳转到指定图片 |
| Ctrl+X | 预标注当前图 |
| Ctrl+Shift+X | 批量预标注（按当前分类筛选） |
| F | 适应窗口 |
| T | 切换标签显示（全部 → 精简 → 隐藏） |
| 1~9 | 快速选择标签类别 |

切换图片、关闭程序时会自动保存当前图的标注变更。

## 项目结构

```
├── main.py              # 入口
├── core/                # 数据模型、项目状态、撤销重做
├── io_ops/              # 读写标注、预标注、导出
├── ui/                  # 界面组件
├── utils/               # 常量、几何、颜色等工具
├── tests/               # 单元测试
├── build.spec           # PyInstaller 打包配置（可选）
└── requirements.txt
```

## 打包为 exe（可选，本地使用）

```bash
build_exe.bat
```

生成的 `dist/`、`build/` 目录已在 `.gitignore` 中忽略，不会提交到仓库。

## 运行测试

```bash
python -m unittest discover -s tests -p "test_*.py"
```

## 许可证

本项目代码供个人学习与内部使用。使用前请自行确认数据集与模型权重的授权。
