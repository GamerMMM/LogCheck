# 日志检索
## 基本功能
### 导入

1. 菜单栏“文件”中导入（可以导入单个文件，也可以批量导入文件）

   <img src="D:\LSBT\LogCheck\LogCheck\img\upload_file.png" alt="upload_file" style="zoom:40%;" />

2. 拖拽导入

### 搜索

1. 键入关键词搜索

   1. 根据两个文本框中的提示语分别键入保留与排除的关键词
   2. 点击“应用过滤”

2. 正则表达式搜索：按照引导框键入正则表达式搜索即可

   <img src="D:\LSBT\LogCheck\LogCheck\img\regex_search.png" alt="regex_search" style="zoom:40%;" />

### 组件说明

1. 勾选 check button 支持忽略大小写（默认忽略）、整词匹配、对所有分页（多个文件时）
2. 勾选“显示结果行”实时更新
3. “重置词条”清空搜索记录

### 结果下载

<img src="D:\LSBT\LogCheck\LogCheck\img\download_res.png" alt="download_res" style="zoom:40%;" />

结果包括两个txt文件夹，直接保存到选定路径：

1. {name}_info.txt：词条过滤信息（包括过滤条件与逻辑说明）
2. {name}_result.txt：词条过滤后的结果（只有结果行）
