import { Card, CardContent } from "@/components/ui/card";
import { AlertCircle } from "lucide-react";
import { Link } from "wouter";

export default function NotFound() {
  return (
    <div className="h-full w-full flex items-center justify-center">
      <Card className="w-full max-w-md mx-4 border-border/50">
        <CardContent className="pt-6">
          <div className="flex mb-4 gap-2 items-center">
            <AlertCircle className="h-6 w-6 text-destructive" />
            <h1 className="text-lg font-bold text-foreground">404 — Page Not Found</h1>
          </div>
          <p className="text-sm text-muted-foreground">
            The page you're looking for doesn't exist.
          </p>
          <Link href="/">
            <span className="text-sm text-primary hover:underline mt-3 inline-block cursor-pointer">
              Return to Overview
            </span>
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}
