from __future__ import annotations

DEFAULT_WEEK_TEMPLATES = {
    "regular": {
        "emoji": "🎀",
        "name": "闪光日常周",
        "description": "认真生活，积攒可爱",
        "theme": "🎀 又是元气满满的一周",
        "goals": ["保持节奏", "记录小确幸", "多喝水"],
        "daily_hints": {
            "monday": "✨ 扎起头发，干劲满满！",
            "tuesday": "💪 保持专注的自己超酷",
            "wednesday": "🥤 奖励自己一杯奶茶吧",
            "thursday": "📅 进度条快跑完啦",
            "friday": "🎉 心已经飞到周末了",
            "saturday": "💤 睡到自然醒的幸福",
            "sunday": "🌸 整理心情，期待下周"
        },
        "suggested_activities": {
            "weekday": ["认真搬砖", "敷面膜", "记手帐"],
            "weekend": ["公园野餐", "看电影", "大扫除"]
        }
    },
    "sprint": {
        "emoji": "💫",
        "name": "变身女战士周",
        "description": "虽然很累，但要闪闪发光",
        "theme": "💫 冲鸭！你可以的！",
        "goals": ["搞定大项目", "不拖延", "奖励自己"],
        "daily_hints": {
            "monday": "🔥 开启战斗模式！",
            "tuesday": "⚡ 效率Max，谁都别拦我",
            "wednesday": "🏋️‍♀️ 坚持住，胜利在望",
            "thursday": "🎯 就差最后一点点啦",
            "friday": "🏆 我真的太棒了！",
            "saturday": "💆‍♀️ 辛苦啦，去做个SPA",
            "sunday": "🛌 彻底躺平回血中"
        },
        "suggested_activities": {
            "weekday": ["番茄钟专注", "断网工作"],
            "weekend": ["疯狂补觉", "买买买"]
        }
    },
    "relax": {
        "emoji": "☁️",
        "name": "软绵绵治愈周",
        "description": "像云朵一样放松，放慢节奏",
        "theme": "☁️ 慢吞吞也没关系",
        "goals": ["睡饱饱", "没烦恼", "听从身体的声音"],
        "daily_hints": {
            "monday": "🧸 允许自己赖个床",
            "tuesday": "☕ 泡一杯热可可",
            "wednesday": "🎨 做点无用但开心的事",
            "thursday": "🌿 晒晒太阳发发呆",
            "friday": "🎵 哼着歌下班/放学",
            "saturday": "🍰 甜食是治愈良药",
            "sunday": "🕯️ 点上香薰，晚安"
        },
        "suggested_activities": {
            "weekday": ["冥想", "泡澡", "看治愈番"],
            "weekend": ["撸猫", "周边游", "烘焙"]
        }
    },
    "social": {
        "emoji": "🍰",
        "name": "甜度满分周",
        "description": "和喜欢的人在一起，收集快乐",
        "theme": "🍰 就要和姐妹贴贴",
        "goals": ["约会", "探店", "拍美照", "八卦聊天"],
        "daily_hints": {
            "monday": "👋 给闺蜜发个表情包",
            "tuesday": "👗 想想周末穿哪条裙子",
            "wednesday": "🎫 订好了展览/电影票",
            "thursday": "🥂 小周末，喝一杯？",
            "friday": "💄 妆容精致，准备出发",
            "saturday": "📸 咔嚓！记录美好瞬间",
            "sunday": "📱 互传照片，修图P图"
        },
        "suggested_activities": {
            "weekday": ["午餐八卦", "视频通话"],
            "weekend": ["下午茶", "剧本杀", "KTV"]
        }
    },
    "recovery": {
        "emoji": "🛁",
        "name": "抱抱自己周",
        "description": "电量低，需要温柔充电",
        "theme": "🛁 拒绝内耗，只爱自己",
        "goals": ["护肤SPA", "情绪排毒", "养生"],
        "daily_hints": {
            "monday": "🌱 对自己温柔一点",
            "tuesday": "🥗 吃点清淡健康的",
            "wednesday": "🛑 拒绝不合理的请求",
            "thursday": "🧘‍♀️ 做个舒缓瑜伽",
            "friday": "💤 今晚十点就睡觉",
            "saturday": "📚 躲进书里的世界",
            "sunday": "🧖‍♀️ 泡个澡，洗去疲惫"
        },
        "suggested_activities": {
            "weekday": ["早睡早起", "写日记"],
            "weekend": ["全身按摩", "中医调理"]
        }
    },
    "holiday": {
        "emoji": "🎡",
        "name": "逃离地球周",
        "description": "去当在逃公主，尽情玩耍",
        "theme": "🎡 假期余额充足",
        "goals": ["尽情玩耍", "制造回忆", "不看工作消息"],
        "daily_hints": {
            "monday": "✈️ 出发！去那个地方",
            "tuesday": "🌴 墨镜一戴，谁都不爱",
            "wednesday": "🎢 尖叫声在哪里！",
            "thursday": "🍧 每一口都是幸福",
            "friday": "🎁 挑点可爱的伴手礼",
            "saturday": "👨‍👩‍👧 和家人的温馨时光",
            "sunday": "😌 手机相册爆满啦"
        },
        "suggested_activities": {
            "weekday": ["旅行", "环球影城/迪士尼"],
            "weekend": ["家庭聚餐", "整理Vlog"]
        }
    },
    "study": {
        "emoji": "🦄",
        "name": "闭关修炼周",
        "description": "偷偷变强，然后惊艳所有人",
        "theme": "🦄 本周谢绝邀约",
        "goals": ["专注模式", "知识进脑子", "拒绝无效社交"],
        "daily_hints": {
            "monday": "📅 制定一个完美的计划",
            "tuesday": "🧠 脑细胞在这个时刻燃烧",
            "wednesday": "📝 笔记记得漂漂亮亮",
            "thursday": "🔄 复习一下，温故知新",
            "friday": "🧩 搞定了一个难点！",
            "saturday": "✍️ 模拟考也要认真对待",
            "sunday": "📊 奖励自己一顿好吃的"
        },
        "suggested_activities": {
            "weekday": ["图书馆", "背单词", "刷网课"],
            "weekend": ["错题复盘", "文具整理"]
        }
    },
    "gaming": {
        "emoji": "🎮",
        "name": "异世界冒险周",
        "description": "在虚拟世界拯救宇宙，或者只是种种田",
        "theme": "🎮 沉浸式二次元",
        "goals": ["快乐联机", "全收集", "推进剧情", "抽卡欧皇"],
        "daily_hints": {
            "monday": "🕹️ 上线领个签到礼包",
            "tuesday": "⚔️ 这个BOSS有点难打",
            "wednesday": "🤝 呼叫队友，请求支援",
            "thursday": "🗺️ 跑图看风景ing",
            "friday": "🌃 熬夜修仙，快乐无边",
            "saturday": "👹 终于通关啦！",
            "sunday": "🏆 整理我的游戏截图"
        },
        "suggested_activities": {
            "weekday": ["做日常", "看攻略"],
            "weekend": ["通宵联机", "清理支线"]
        }
    }
}
