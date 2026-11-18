package com.zeyu.phoneai.assistant;

public final class PageTranslationResultTest {
    public static void main(String[] args) throws Exception {
        java.util.ArrayList<String> result=PageTranslationResult.parse(" [[P1]]\n你好\n[[P2]]\n第二段\n继续 ", 2);
        if (!result.get(0).equals("你好") || !result.get(1).equals("第二段\n继续"))
            throw new AssertionError("Block contents changed");
        for (String invalid: new String[]{"[[P2]]甲[[P1]]乙", "[[P1]]甲", "[[P1]]甲[[P2]]乙[[P3]]丙",
                "[[P1]]甲[[P1]]乙", "[[P1]]甲[[P2]] ", "额外说明[[P1]]甲[[P2]]乙",
                "[[P99999999999999]]甲", "[[P01]]甲[[P2]]乙", "甲乙", ""}) {
            boolean rejected=false;
            try { PageTranslationResult.parse(invalid, 2); } catch (Exception expected) { rejected=true; }
            if (!rejected) throw new AssertionError("Unsafe block mapping accepted: "+invalid);
        }
        System.out.println("Page translation: valid multiline blocks and 10 ambiguous/incomplete responses checked");
    }
}
