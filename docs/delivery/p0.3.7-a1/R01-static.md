# R01-1 公开资源边界整改

基线b029f78已用登记test的生成MP4复现：public目录中的改名硬链接GET200/Range206，响应实际字节一致，生产与Demo均复制该文件。不是工业原片或正式封存资料。

禁用Vite默认publicDir和隐式复制。受审清单browser-files.json逐文件固定浏览器源码、公开配置/知识/schema和专用static资源的SHA；未知文件不因位于某个父目录而获准。资源在HTTP(GET/HEAD/Range/raw/url/import)、模块解析/加载、HTML转换及构建边界统一检查；硬链接计数、符号/路径别名先于内容读取，已列名的文件被替换也拒绝。生产/Demo最终成员只允许单文件index和逐项声明、摘要匹配的static资源，默认public的任意成员不发布。

168项真实请求变体通过；改名硬链接(.txt/.js)、符号链接、普通未知类型、源码目录别名均被拒绝。正常React和合法SVG通过；两种构建只含index.html与static/brand-mark.svg，无受保护完整字节或base64；已列名SVG被替换为硬链接时构建失败。缓存无受保护字节。当前前端99项并行测试、双构建通过。

信任边界：受审Git清单/构建配置及lockfile安装的执行依赖为可信代码，不是可写的媒体发布目录；有权同时改清单和源码的管理员仍需独立发布审查。更新合法配置/schema后必须显式刷新清单并审核diff，不自动扩大目录或按文件名例外放行。历史Demo及实验不覆盖。

生产服务复核：新增build-members.json，由合法构建记录实际index/SVG摘要；ASGI静态服务只按该清单返回独立普通文件，构建后再放入的未知文件、改名硬/符号链接也拒绝，已登记index被链接替换同样拒绝。缺失或非法构建清单时API仍可用、静态资源fail closed。新增2项生产服务反例通过。最终双构建成员为index.html、static/brand-mark.svg、build-members.json（元数据本身不通过静态API返回）；168项静态变体及无保护字节/缓存复验再次通过。

发布闭包最终复核：依赖不再按父目录加扩展名放行，lockfile绑定到逐项安装文件摘要；未知node_modules别名也拒绝，优化缓存仅认可Vite实际声明且来源摘要匹配的成员。Vite preview另按构建成员摘要门控，构建后硬链接的GET/HEAD/Range及raw/url/import变体均404。开发拒绝变体扩大为216项，preview8项；正常React/知识/合法SVG、双构建、污染已列资产失败及缓存无保护内容再次通过。依赖安装与审批清单是可信发布输入，不是任意代码执行的沙箱。
